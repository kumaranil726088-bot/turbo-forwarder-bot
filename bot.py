"""Turbo Forwarder V6.2 — saved-session recovery and resilient private watches.
Use only for content you are authorized to copy. Telegram permissions still apply.
"""
import asyncio
import contextlib
import copy
import gc
import html
import io
import json
import logging
import os
import re
import resource
import shutil
import sys
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from telethon import TelegramClient, events, Button, utils
from telethon.sessions import StringSession
from telethon.extensions import html as tl_html
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetForumTopicsRequest, CreateForumTopicRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault, BotCommandScopePeer, DocumentAttributeFilename, Channel
from telethon.errors import SessionPasswordNeededError, FloodWaitError

import control_ui
from media_io import download_original
from help_ui import help_page, SLASH_MENU, OWNER_MENU
from remote_state import GitState, Checkpoints, StateConflict
from sessions import SessionRecovery, LoginRequired, SessionUnavailable, AUTH_ERRORS
from storage import Store, DEFAULTS
from transfer import Parallel, TYPES, parse_links, parse_post_link, source_ref, destination, msg_type, topic_id, renamed, retry

from dotenv import load_dotenv
load_dotenv()

VERSION = '6.2'
START = time.time()
API_ID = int(os.getenv('API_ID','0'))
API_HASH = os.getenv('API_HASH','')
BOT_TOKEN = os.getenv('BOT_TOKEN','')
OWNER_ID = int(os.getenv('OWNER_ID','8841527686'))
ROOT = Path(os.getenv('DOWNLOAD_DIR','downloads')).resolve()
DB_PATH = os.getenv('DB_PATH','botdata.db')
MAX_FILE = int(os.getenv('MAX_FILE_MB','1900'))*1024*1024
DISK_BUDGET = int(os.getenv('DISK_BUDGET_MB','3000'))*1024*1024
MAX_SESSIONS = int(os.getenv('MAX_SESSIONS','20'))
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('turbo')
# Do not log credentials or message contents.
logging.getLogger('telethon').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)
BOT = None
DB = None
USER_CLIENTS = {}
LOGINS = {}
PANELS = {}
JOBS = {}
WATCH_TASKS = {}
USER_LOCKS = {}
BACKGROUND = set()
TRANSFER_LIMIT = None
USERNAME = ''
STOPPING = False
CHECKPOINTS = None
RECOVERY = None
SUPPORT_URL = 'https://t.me/SyntaxError_2'
SYNC_WARNING_AT = 0


def spawn(coro):
    task = asyncio.create_task(coro)
    BACKGROUND.add(task)
    def finished(t):
        BACKGROUND.discard(t)
        if not t.cancelled() and t.exception():
            log.error('Background failure: %s',type(t.exception()).__name__)
    task.add_done_callback(finished)
    return task


def lock(uid):
    return USER_LOCKS.setdefault(uid,asyncio.Lock())


def cfg_for(uid):
    cfg = {k:DB.get(uid,k) for k in DEFAULTS}
    cfg['target'] = DB.get(uid,'target',str(uid))
    for k in ('promo','rename','hyperlink','topic','thumb'):
        cfg[k] = DB.get(uid,k,'')
    return cfg


async def respond(e,text,buttons=None):
    if len(text.encode('utf-16-le'))//2>3900:
        # Telegram limits message length in UTF-16 units; split even emoji-heavy output safely.
        chunks=[]
        while text:
            cut=min(1800,len(text))
            if cut<len(text):
                boundary=text.rfind('\n',0,cut)
                if boundary>cut//2:
                    cut=boundary+1
            chunks.append(text[:cut]);text=text[cut:]
        result=None
        for i,chunk in enumerate(chunks):
            result=await e.respond(chunk,parse_mode=None,link_preview=False,buttons=buttons if i==len(chunks)-1 else None)
        return result
    kw = dict(parse_mode=None,buttons=buttons,link_preview=False)
    if isinstance(e,events.CallbackQuery.Event):
        try:
            return await e.edit(text,**kw)
        except Exception:
            pass
    return await e.respond(text,**kw)


async def notify(uid,text,**kw):
    try:
        return await retry(BOT.send_message,uid,text,parse_mode=None,**kw)
    except Exception as exc:
        log.warning('Notification failed for %s: %s',uid,type(exc).__name__)


async def entity(cl,ref):
    value = int(ref) if str(ref).lstrip('-').isdigit() else ref
    try:
        return await retry(cl.get_entity,value)
    except ValueError:
        await retry(cl.get_dialogs,limit=None)
        return await retry(cl.get_entity,value)


def support_buttons():
    return [[Button.url('🆘 Contact @SyntaxError_2',SUPPORT_URL),Button.inline('📖 Help',b'help_home')]]


async def ensure_session(uid,optional=False,force=False):
    if RECOVERY:
        return await RECOVERY.ensure(uid,optional=optional,force=force)
    # Tests and local deployments can inject a registry without creating a manager.
    if uid in USER_CLIENTS:
        return USER_CLIENTS[uid]
    saved=DB.get(uid,'session')
    if saved:
        return await connect_user(uid,saved)
    if optional:
        return None
    raise LoginRequired('Connect your own Telegram account once with /login.')


def storage_status():
    if not CHECKPOINTS:
        return 'local persistent disk' if os.getenv('PERSISTENT_STORAGE')=='1' else 'ephemeral (no remote checkpoints configured)'
    remote=CHECKPOINTS.remote
    age=int(time.time()-remote.last_success) if remote.last_success else None
    return ('⚠ '+remote.error if remote.error else 'Encrypted remote checkpoint OK')+f' | last sync: {age}s ago'


async def durable_flush(critical=False):
    global SYNC_WARNING_AT
    if not CHECKPOINTS:return True
    try:
        await CHECKPOINTS.flush()
        return True
    except Exception as exc:
        log.error('Encrypted state sync failed: %s',type(exc).__name__)
        if time.time()-SYNC_WARNING_AT>600:
            SYNC_WARNING_AT=time.time()
            await notify(OWNER_ID,'⚠ Durable state sync failed: '+type(exc).__name__+'. Local service continues, but new changes are NOT yet safe across restart. /admin → Storage. Check GitHub/key/network. No saved sessions were deleted.',buttons=support_buttons())
        return False


async def fetcher(uid,src):
    if uid in USER_CLIENTS or DB.get(uid,'session'):
        return await ensure_session(uid)
    if str(src).lstrip('-').isdigit():
        raise LoginRequired('Private sources require your own saved account and channel membership. /login once.')
    ent = await entity(BOT,src)
    if not isinstance(ent,Channel) or not getattr(ent,'username',None):
        raise ValueError('Without login, sources must be public channels/supergroups, never bot private conversations')
    return BOT


async def destination_entity(uid,cfg):
    ref,topic = destination(cfg.get('target') or str(uid))
    topic = int(cfg.get('topic') or topic or 0) or None
    sender = await ensure_session(uid) if cfg.get('sender') == 'User' else BOT
    # Shared bot admin rights must never grant an unrelated user access to a chat.
    if ref != str(uid):
        own = await ensure_session(uid)
        own_ent = await entity(own,ref)
        rights = await retry(own.get_permissions,own_ent,'me')
        if getattr(own_ent,'broadcast',False) and not (getattr(rights,'is_admin',False) or getattr(rights,'is_creator',False)):
            raise ValueError('You must be an admin of the destination channel')
        if getattr(rights,'is_banned',False) or getattr(rights,'send_messages',True) is False:
            raise ValueError('Your account cannot write to this destination')
    if sender is not BOT and ref == str(uid):
        dest = await sender.get_me()  # User sender saves to Saved Messages.
    else:
        dest = await entity(sender,ref)
    return sender,dest,topic


def caption(m,cfg,index):
    original = m.message or ''
    text = original
    for old,new in cfg.get('replacements',[]):
        if old:
            text = text.replace(str(old),str(new))
    name = getattr(getattr(m,'file',None),'name',None) or ''
    tpl = cfg.get('caption')
    if tpl:
        text = str(tpl).replace('{original}',text).replace('{caption}',text).replace('{index}',str(index)).replace('{title}',name)
    rule = cfg.get('rename','')
    if rule and '|' in rule:
        old,new = rule.split('|',1)
        if old:
            text = text.replace(old,new)
    if cfg.get('promo'):
        text += '\n\n'+cfg['promo']
    if text == original and not cfg.get('hyperlink'):
        return text,list(m.entities or [])
    # User caption templates accept Telegram HTML; original content is escaped.
    # Transformations use plain text unless an explicit hyperlink is requested.
    link = cfg.get('hyperlink','')
    if link and '|' in link:
        label,url = link.split('|',1)
        if not re.match(r'^https?://',url):
            raise ValueError('Hyperlink must use https:// or http://')
        escaped = html.escape(text)
        if label == 'all':
            escaped = re.sub(r'https?://[^\s<]+',lambda x:'<a href="'+html.escape(url,quote=True)+'">'+x[0]+'</a>',escaped)
        else:
            escaped = escaped.replace(html.escape(label),'<a href="'+html.escape(url,quote=True)+'">'+html.escape(label)+'</a>')
        return tl_html.parse(escaped)
    return text,[]


async def messages_group(cl,src,mid):
    msg = await retry(cl.get_messages,src,ids=int(mid))
    if not msg or getattr(msg,'action',None) or not getattr(msg,'message',None) and not getattr(msg,'media',None):
        return []
    if msg.grouped_id:
        around = await retry(cl.get_messages,src,ids=list(range(max(1,msg.id-9),msg.id+10)))
        return sorted([m for m in around if m and m.grouped_id == msg.grouped_id],key=lambda m:m.id)
    return [msg]


def disk_usage():
    return sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file()) if ROOT.exists() else 0


async def prepare(uid,src,mid,cfg,index,folder,seen):
    cl = await fetcher(uid,src)
    source = await entity(cl,src)
    group = await messages_group(cl,source,mid)
    if not group:
        return {'skip':True,'ids':[mid]}
    all_ids=[m.id for m in group]
    key = (str(src), group[0].grouped_id or group[0].id)
    if key in seen:
        return {'skip':True,'ids':all_ids}
    seen.add(key)
    delivered=set(cfg.get('_delivered_ids',[]))
    group = [m for m in group if m.id not in delivered and msg_type(m) in cfg['type_filters'] and (cfg['filter'] != 'text' or msg_type(m) == 'text')]
    if not group:
        return {'skip':True,'ids':all_ids}
    changed_file = bool(cfg.get('rename') or cfg.get('thumb'))
    restricted = getattr(source,'noforwards',False) or any(getattr(m,'noforwards',False) for m in group)
    # Files are copied with their original session, not a cross-account access hash.
    sender = USER_CLIENTS.get(uid) if cfg.get('sender') == 'User' else BOT
    direct = cl is sender and not restricted and not changed_file and cfg['mode'] != 'download'
    result = dict(cl=cl,source=source,src_ref=src,msgs=group,files=[],thumbs=[],direct=direct,ids=all_ids,index=index)
    if direct or not any(m.file for m in group):
        return result
    folder.mkdir(parents=True,exist_ok=True)
    async with TRANSFER_LIMIT:
        for m in group:
            if not m.file:
                continue
            size = m.file.size or 0
            if size > MAX_FILE:
                raise ValueError('File exceeds configured MAX_FILE_MB')
            if disk_usage()+size > DISK_BUDGET or shutil.disk_usage(ROOT).free < size+64*1024*1024:
                raise ValueError('Disk budget reached; reduce workers/file size')
            name = m.file.name or ('media_'+str(m.id)+(m.file.ext or '.bin'))
            media_dir = folder/str(m.id)
            media_dir.mkdir(exist_ok=True)
            path = media_dir/renamed(name,cfg.get('rename',''),index)
            await download_original(cl,m,path,cfg['connections'])
            thumb = None
            if cfg.get('thumb'):
                candidate = ROOT/f'u_{uid}'/'thumb.jpg'
                if candidate.is_file():
                    thumb = str(candidate)
                else:
                    log.info('Custom thumbnail unavailable for uid=%s; using source thumbnail',uid)
            if not thumb and (m.video or m.document):
                try:
                    thumb = await retry(cl.download_media,m,file=str(folder/f'{m.id}_thumb.jpg'),thumb=-1)
                except Exception:
                    pass
            result['files'].append((m,str(path)))
            result['thumbs'].append(thumb)
    return result


async def send_media(sender,dest,media,captions,entities,topic):
    if len(media) == 1:
        return await retry(sender.send_file,dest,media[0],caption=captions[0],
                           formatting_entities=entities[0],parse_mode=None,reply_to=topic,supports_streaming=True)
    return await retry(sender.send_file,dest,media,caption=captions,formatting_entities=entities,
                       parse_mode=None,reply_to=topic,supports_streaming=True)


async def send_prepared(uid,item,cfg,sender,dest,topic):
    if item.get('skip'):
        return 0
    msgs = item['msgs']
    group = [m for m in msgs if m.file]
    texts = [caption(m,cfg,item['index']) for m in msgs]
    # Native forward only within one session and when there are no customizations.
    custom = any(cfg.get(k) for k in ('caption','promo','rename','hyperlink','thumb','replacements'))
    if item['direct'] and cfg['mode'] == 'forward' and not custom and not topic:
        try:
            await retry(sender.forward_messages,dest,[m.id for m in msgs],from_peer=item['source'])
            return len(msgs)
        except FloodWaitError:
            raise
        except Exception:
            # Permission changes between download and send: fall back to extraction.
            item['direct'] = False
    if any(m.poll for m in msgs):
        raise ValueError('Poll transfer needs User sender, forward mode, no customizations and no destination topic')
    if item['direct']:
        try:
            if group:
                await send_media(sender,dest,[m.media for m in group],[caption(m,cfg,item['index'])[0] for m in group],
                                     [caption(m,cfg,item['index'])[1] for m in group],topic)
            else:
                for text,entities in texts:
                    if text:
                        await retry(sender.send_message,dest,text,formatting_entities=entities,parse_mode=None,reply_to=topic,link_preview=False)
            return len(msgs)
        except FloodWaitError:
            raise
        except Exception:
            item['direct'] = False
    if group and not item['files']:
        # Restricted forwarding or cached-media copy rejected. Re-prepare with extraction.
        folder = ROOT/f'u_{uid}_{uuid.uuid4().hex[:12]}'
        try:
            fallback = await prepare(uid,item.get('src_ref',str(utils.get_peer_id(item['source']))),msgs[0].id,dict(cfg,mode='download'),item['index'],folder,set())
            return await send_prepared(uid,fallback,dict(cfg,mode='download'),sender,dest,topic)
        finally:
            shutil.rmtree(folder,ignore_errors=True)
    if item['files']:
        uploads = []
        for (m,path),thumb in zip(item['files'],item['thumbs']):
            async with TRANSFER_LIMIT:
                try:
                    if os.path.getsize(path) < 1024*1024:
                        raise ValueError('Small file')
                    uploaded = await Parallel(sender,cfg['connections']).upload(path)
                except (asyncio.CancelledError,FloodWaitError):
                    raise
                except Exception as exc:
                    log.info('Standard upload fallback: %s',type(exc).__name__)
                    uploaded = await retry(sender.upload_file,path)
                # upload_media converts per-file attributes and thumbnails into album-capable media.
                attrs = [copy.copy(a) for a in (getattr(m.document,'attributes',[]) if m.document else [])]
                for attr in attrs:
                    if isinstance(attr,DocumentAttributeFilename):
                        attr.file_name = Path(path).name
                    if hasattr(attr,'supports_streaming') and m.video:
                        attr.supports_streaming = True
                media = await sender._file_to_media(uploaded,force_document=not bool(m.photo or m.video or m.audio or m.voice or m.gif),
                    attributes=attrs,as_image=bool(m.photo),mime_type=m.file.mime_type,
                    thumb=thumb,supports_streaming=bool(m.video),voice_note=bool(m.voice))
                uploads.append(media[1])
        await send_media(sender,dest,uploads,[caption(m,cfg,item['index'])[0] for m,p in item['files']],
                         [caption(m,cfg,item['index'])[1] for m,p in item['files']],topic)
        return len(item['files'])
    for m,(text,entities) in zip(msgs,texts):
        if m.poll:
            raise ValueError('Poll cloning is not supported by Telegram bot sessions; use User sender + forward mode')
        if text:
            await retry(sender.send_message,dest,text,formatting_entities=entities,parse_mode=None,reply_to=topic,link_preview=False)
    return len(msgs)


async def run_job(uid,links,cfg=None,start=0,topic_map=None):
    cfg = cfg or cfg_for(uid)
    job = JOBS[uid]
    folder = ROOT/f'u_{uid}_{job["id"]}'
    pending_tasks = {}
    seen = set()
    try:
        await durable_flush(critical=True)
        sender,dest,default_topic = await destination_entity(uid,cfg)
        workers = max(1,min(5,int(cfg['workers'])))
        async with lock(uid):
            for i in range(start,len(links)):
                if job['cancel']:
                    break
                for j in range(i,min(i+workers,len(links))):
                    if j not in pending_tasks:
                        src,mid = links[j]
                        pending_tasks[j] = asyncio.create_task(prepare(uid,src,mid,cfg,j+1,folder/str(j),seen))
                task = pending_tasks.pop(i)
                # Keep a reference so cancellation also joins an in-flight preparation.
                job['preparing'] = task
                item = await task
                job.pop('preparing',None)
                topic = default_topic
                if topic_map and not item.get('skip'):
                    topic = topic_map.get(str(topic_id(item['msgs'][0])),default_topic)
                n = await send_prepared(uid,item,cfg,sender,dest,topic)
                job['ok'] += n
                DB.success(uid,n)
                job['done'] = i+1
                DB.set(uid,'pending_job',dict(links=links,cfg=cfg,start=i+1,topic_map=topic_map))
                shutil.rmtree(folder/str(i),ignore_errors=True)
                if time.time()-job['updated'] > 5:
                    await update_card(uid)
                await asyncio.sleep(max(0.1,float(cfg['delay'])))
        if not job['cancel']:
            DB.set(uid,'pending_job',None)
            job['state'] = 'Complete'
        else:
            job['state'] = 'Stopped; /resume available'
    except asyncio.CancelledError:
        job['state'] = 'Stopped; /resume available'
    except Exception as exc:
        if RECOVERY and isinstance(exc,AUTH_ERRORS+(LoginRequired,)):
            RECOVERY.state[uid]={'state':'revoked','error':type(exc).__name__}
        job['state'] = 'Failed: '+type(exc).__name__
        await notify(uid,'Transfer paused: '+str(exc)[:400]+'\nFix settings, then /resume. Telegram permissions still apply.')
    finally:
        for t in list(pending_tasks.values())+[job.get('preparing')]:
            if t and not t.done():
                t.cancel()
        await asyncio.gather(*(t for t in list(pending_tasks.values())+[job.get('preparing')] if t),return_exceptions=True)
        shutil.rmtree(folder,ignore_errors=True)
        await update_card(uid,final=True)
        JOBS.pop(uid,None)


async def update_card(uid,final=False):
    job = JOBS.get(uid)
    if not job:
        return
    job['updated'] = time.time()
    elapsed = max(1,time.time()-job['started'])
    text = f"⚡ V6 Transfer — {job['state']}\nProcessed: {job['done']}/{job['total']}\nMessages sent: {job['ok']}\nElapsed: {elapsed:.1f}s | {job['ok']/elapsed:.2f} msg/s\n/status · /stop · /resume"
    try:
        if job.get('card'):
            await job['card'].edit(text,parse_mode=None,buttons=None if final else [[Button.inline('⏹ Stop my job',b'stopjob')]])
    except Exception:
        pass


async def start_job(e,links,cfg=None,start=0,topic_map=None):
    uid = e.sender_id
    if uid in JOBS:
        return await respond(e,'Your job is already running. /stop first.')
    if not links or len(links)>10000:
        raise ValueError('Choose between 1 and 10,000 posts')
    complete_cfg = cfg_for(uid)
    complete_cfg.update(cfg or {})
    cfg = complete_cfg
    card = await respond(e,'⚡ Validating your destination and starting your isolated queue…')
    JOBS[uid] = dict(id=uuid.uuid4().hex[:12],ok=0,done=start,total=len(links),cancel=False,started=time.time(),updated=0,state='Running',card=card)
    DB.set(uid,'pending_job',dict(links=links,cfg=cfg,start=start,topic_map=topic_map))
    JOBS[uid]['task'] = spawn(run_job(uid,links,cfg,start,topic_map))


async def stop_job(uid):
    job = JOBS.get(uid)
    if job:
        job['cancel'] = True
        job['task'].cancel()
        await asyncio.gather(job['task'],return_exceptions=True)
        # A task cancelled before its first instruction does not execute its finally block.
        JOBS.pop(uid,None)


async def connect_user(uid,session):
    if not DB.cipher:
        raise ValueError('SESSION_KEY is required for session import/login')
    if uid not in USER_CLIENTS and len(USER_CLIENTS)>=MAX_SESSIONS:
        raise ValueError('Server session capacity reached; owner can increase MAX_SESSIONS')
    cl = TelegramClient(StringSession(session),API_ID,API_HASH,flood_sleep_threshold=0)
    try:
        await cl.connect()
        if not await cl.is_user_authorized():
            raise LoginRequired('Telegram session expired/revoked; /login again')
        me = await cl.get_me()
        if me.id != uid:
            raise ValueError('Session must belong to the Telegram account messaging this bot')
        # Entity warming is optional; a transient dialog failure must not discard a valid login.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(cl.get_dialogs(limit=200),30)
        if uid not in USER_CLIENTS and len(USER_CLIENTS)>=MAX_SESSIONS:
            raise SessionUnavailable('Saved login retained; concurrent session capacity is currently full')
        previous=USER_CLIENTS.get(uid)
        USER_CLIENTS[uid] = cl
        if previous and previous is not cl:
            with contextlib.suppress(Exception):
                await previous.disconnect()
        async def on_new(event):
            if STOPPING or not DB.allowed(uid):
                return
            for route in DB.get(uid,'watch',[]):
                if str(event.chat_id) == str(route.get('src')) and not route.get('paused'):
                    kick_watch(uid,route['id'])
        cl.add_event_handler(on_new,events.NewMessage(incoming=True))
        # Outgoing messages in watched channels also count (loop destinations are rejected).
        cl.add_event_handler(on_new,events.NewMessage(outgoing=True))
        saved=cl.session.save()
        if DB.get(uid,'session')!=saved:
            DB.set(uid,'session',saved)
        if RECOVERY:RECOVERY.connected(uid)
        return cl
    except BaseException:
        if USER_CLIENTS.get(uid) is not cl:
            await cl.disconnect()
        raise


async def disconnect_user(uid,revoke=False):
    await stop_job(uid)
    tasks = [t for (u,r),t in list(WATCH_TASKS.items()) if u == uid]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    cl = USER_CLIENTS.pop(uid,None)
    if cl:
        if revoke:
            with contextlib.suppress(Exception):
                await cl.log_out()
        await cl.disconnect()


def kick_watch(uid,rid,force=False):
    route=next((r for r in DB.get(uid,'watch',[]) if r.get('id')==rid),None)
    if not route or route.get('paused') or route.get('flood_until',0)>time.time():return
    if not force and route.get('retry_at',0)>time.time():return
    key = (uid,rid)
    if key not in WATCH_TASKS or WATCH_TASKS[key].done():
        WATCH_TASKS[key] = spawn(drain_watch(uid,rid))


def save_route(uid,route):
    routes = DB.get(uid,'watch',[])
    for i,r in enumerate(routes):
        if r['id'] == route['id']:
            routes[i] = route
            DB.set(uid,'watch',routes)
            return True
    return False


async def drain_watch(uid,rid):
    await asyncio.sleep(0.7)  # album settling; single messages do not wait for polling.
    folder = ROOT/f'u_{uid}_watch_{rid}'
    try:
        async with lock(uid):
            routes = DB.get(uid,'watch',[])
            route = next((r for r in routes if r['id']==rid),None)
            if not route or route.get('paused') or not DB.allowed(uid):
                return
            cl = await ensure_session(uid)
            cfg = cfg_for(uid)
            cfg['target'] = route['target']
            cfg['topic'] = route.get('topic')
            sender,dest,topic = await destination_entity(uid,cfg)
            src = await entity(cl,route['src'])
            seen = set()
            async for m in cl.iter_messages(src,min_id=int(route.get('last',0)),reverse=True):
                current = next((r for r in DB.get(uid,'watch',[]) if r['id']==rid),None)
                if not current or current.get('paused') or STOPPING or not DB.allowed(uid):
                    break
                if m.id <= route.get('last',0):
                    continue
                recent=route.get('recent_ids',[])
                if m.id in recent:
                    route['last']=m.id
                    save_route(uid,route)
                    continue
                cfg['_delivered_ids']=recent
                item = await prepare(uid,route['src'],m.id,cfg,m.id,folder/str(m.id),seen)
                n = await send_prepared(uid,item,cfg,sender,dest,topic)
                DB.success(uid,n)
                # Advance through the iterator, not max(album IDs): interleaved posts must not be skipped.
                route['last'] = m.id
                route['recent_ids']=list(dict.fromkeys(recent+item.get('ids',[m.id])))[-200:]
                route['error'] = ''
                route.update(failures=0,retry_at=0,flood_until=0,last_success=time.time())
                if not save_route(uid,route):
                    break
                shutil.rmtree(folder,ignore_errors=True)
                await asyncio.sleep(max(.1,float(cfg['delay'])))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if RECOVERY and isinstance(exc,AUTH_ERRORS+(LoginRequired,)):
            RECOVERY.state[uid]={'state':'revoked','error':type(exc).__name__}
        routes = DB.get(uid,'watch',[])
        route = next((r for r in routes if r['id']==rid),None)
        if route:
            old = route.get('error')
            route['error'] = type(exc).__name__+': '+str(exc)[:200]
            failures=route.get('failures',0)+1
            delay=min(900,5*2**min(failures,8))
            if isinstance(exc,FloodWaitError):
                delay=max(delay,exc.seconds+1)
                route['flood_until']=time.time()+exc.seconds+1
            route.update(failures=failures,retry_at=time.time()+delay)
            should_notify=old!=route['error'] and time.time()-route.get('notice_at',0)>300
            if should_notify:route['notice_at']=time.time()
            save_route(uid,route)
            if should_notify:
                await notify(uid,f"Watch {rid} waiting: {route['error']}\nAutomatic retry in {delay}s. Cursor was NOT skipped.\n/watchlist to inspect. Support: @SyntaxError_2",buttons=support_buttons())
    finally:
        shutil.rmtree(folder,ignore_errors=True)
        WATCH_TASKS.pop((uid,rid),None)


async def poll_loop():
    due = {}
    while not STOPPING:
        for uid in list(USER_CLIENTS):
            if not DB.allowed(uid):
                continue
            if time.monotonic() < due.get(uid,0):
                continue
            due[uid] = time.monotonic()+max(3,float(DB.get(uid,'poll')))
            for route in DB.get(uid,'watch',[]):
                if not route.get('paused'):
                    kick_watch(uid,route['id'])
        await asyncio.sleep(1)


async def add_watch(uid,raw):
    await ensure_session(uid)
    parts = raw.split()
    if not parts:
        raise ValueError('/watch <source or post link> [destination[:topic]]')
    cl = USER_CLIENTS[uid]
    src = await entity(cl,source_ref(parts[0]))
    src_id = str(utils.get_peer_id(src))
    target = parts[1] if len(parts)>1 else DB.get(uid,'target',str(uid))
    cfg = cfg_for(uid)
    cfg.update(target=target,topic=None)
    sender,dest,topic = await destination_entity(uid,cfg)
    dst_id = str(utils.get_peer_id(dest))
    routes = DB.get(uid,'watch',[])
    if src_id == dst_id or any(r['src']==dst_id and r.get('dest_id')==src_id for r in routes):
        raise ValueError('Source/destination loop rejected')
    # Reject longer cycles too.
    graph = {}
    for r in routes:
        graph.setdefault(r['src'],[]).append(r.get('dest_id'))
    todo,visited = [dst_id],set()
    while todo:
        node = todo.pop()
        if node == src_id:
            raise ValueError('Watch cycle rejected')
        if node not in visited:
            visited.add(node)
            todo.extend(graph.get(node,[]))
    if len(routes)>=20:
        raise ValueError('Maximum 20 live routes per user')
    if any(r['src']==src_id and r['target']==target for r in routes):
        raise ValueError('Route already exists')
    latest = await cl.get_messages(src,limit=1)
    route = dict(id=uuid.uuid4().hex[:8],src=src_id,target=target,dest_id=dst_id,topic=topic,
                 last=latest[0].id if latest else 0,paused=False,error='')
    routes.append(route)
    DB.set(uid,'watch',routes)
    return route


def menu():
    return [[Button.inline('🔐 My login',b'menu_login'),Button.inline('⚡ Forward',b'menu_forward')],
            [Button.inline('🔴 Auto-Forward',b'menu_watch'),Button.inline('⚙ My settings',b'menu_settings')],
            [Button.inline('📊 My status',b'menu_status'),Button.inline('❓ Commands',b'menu_help')],
            [Button.url('🆘 Support • @SyntaxError_2',SUPPORT_URL)]]

HELP = help_page('home')[0]  # compatibility for tooling; command pages are served dynamically


def panel_buttons(st):
    return [[Button.inline('Promo',b'pn_promo'),Button.inline('Caption',b'pn_caption')],
            [Button.inline('Rename',b'pn_rename'),Button.inline('Hyperlink',b'pn_hyperlink')],
            [Button.inline('Thumbnail',b'pn_thumb'),Button.inline('Dest topic',b'pn_topic')],
            [Button.inline('Sender: '+st['cfg']['sender'],b'pn_sender')],
            [Button.inline('✅ Save & start',b'pn_save'),Button.inline('Cancel',b'pn_cancel')]]


def panel_text(st):
    cfg = st['cfg']
    count = abs(st['last']-st['first'])+1
    return '✨ Transfer settings\n'+f"Source: {st['src']} | {st['first']} → {st['last']} ({count})\n"+'\n'.join(f'{k}: {cfg.get(k) or "Not set"}' for k in ('target','promo','caption','rename','hyperlink','thumb','topic','sender'))


async def show_panel(e,st):
    st['step'] = 'panel'
    await respond(e,panel_text(st),panel_buttons(st))


async def panel_input(e):
    uid = e.sender_id
    st = PANELS.get(uid)
    if not st:
        return False
    raw = (e.raw_text or '').strip()
    step = st['step']
    if step == 'watch':
        route = await add_watch(uid,raw)
        PANELS.pop(uid,None)
        await respond(e,'🔴 Auto-Forward enabled: '+route['id']+'\n/watchlist')
    elif step == 'first':
        parsed = parse_post_link(raw)
        if not parsed:
            raise ValueError('Send first post link or /cancel')
        st['src'],st['first'] = parsed
        st['step'] = 'last'
        await respond(e,'Send last post link or message ID (same source).')
    elif step == 'last':
        parsed = parse_post_link(raw)
        if parsed and parsed[0] != st['src']:
            raise ValueError('First and last links must have the same source')
        end = parsed[1] if parsed else int(raw)
        if abs(end-st['first'])>=10000 or end < 1:
            raise ValueError('Maximum 10,000 posts; positive IDs only')
        st['first'],st['last'] = sorted((st['first'],end))
        await show_panel(e,st)
    elif step == 'thumb':
        if raw.lower() == 'skip':
            st['cfg']['thumb'] = ''
        elif e.photo or (e.document and e.file and e.file.mime_type == 'image/jpeg'):
            folder = ROOT/f'u_{uid}'
            folder.mkdir(exist_ok=True)
            await e.download_media(file=str(folder/'thumb.jpg'))
            # Telegram requires JPEG <=200KB and <=320px.
            from PIL import Image
            with Image.open(folder/'thumb.jpg') as im:
                im = im.convert('RGB')
                im.thumbnail((320,320))
                im.save(folder/'thumb.jpg','JPEG',quality=80)
            st['cfg']['thumb'] = 'custom'
        else:
            raise ValueError('Send a photo/JPEG thumbnail (not a URL) or skip')
        await show_panel(e,st)
    elif step.startswith('set_'):
        key = step[4:]
        value = '' if raw.lower()=='skip' else raw
        if key == 'topic':
            value = int(value or 0) or None
            if value and value < 1:
                raise ValueError('Topic ID must be positive')
        if key == 'rename' and len(value)>200:
            raise ValueError('Rename rule too long')
        st['cfg'][key] = value
        await show_panel(e,st)
    else:
        return False
    return True


def keypad(code=''):
    rows = [[Button.inline(str(n),f'lg_digit_{n}'.encode()) for n in range(a,a+3)] for a in (1,4,7)]
    rows += [[Button.inline('⌫',b'lg_back'),Button.inline('0',b'lg_digit_0'),Button.inline('✅ Submit',b'lg_submit')],
             [Button.inline('Cancel login',b'lg_cancel')]]
    return rows


async def close_login(uid):
    state = LOGINS.pop(uid,None)
    if state and state.get('cl'):
        await state['cl'].disconnect()


async def start_login(e):
    uid=e.sender_id
    if uid in USER_CLIENTS or DB.get(uid,'session'):
        try:
            await ensure_session(uid,force=True)
            await respond(e,'✅ You are already logged in. Your saved account is connected—no new OTP needed.',menu())
            return False
        except LoginRequired:
            pass
    if not DB.cipher:
        raise ValueError('Owner must configure SESSION_KEY before logins')
    await close_login(e.sender_id)
    LOGINS[e.sender_id] = dict(step='phone',created=time.time())
    await respond(e,'🔐 Your private login\nSend your phone with country code (+91…).\nUse the inline keypad for OTP; never send the plain OTP as a chat message.\nYour session is encrypted at rest. Bot operator can access server-side sessions; only log in if you trust this deployment. /cancellogin')
    return True


async def finish_login(e,state):
    uid = e.sender_id
    cl = state['cl']
    me = await cl.get_me()
    if me.id != uid:
        raise ValueError('Login account must match your Telegram user ID')
    session = cl.session.save()
    await close_login(uid)
    await connect_user(uid,session)
    synced=await durable_flush(critical=True)
    await respond(e,'✅ Your account is connected. '+('Encrypted remote recovery checkpoint saved.' if CHECKPOINTS and synced else '⚠ Remote recovery pending/unconfigured; do not restart before storage is healthy.')+'\n/setchannel or /watch to continue.',menu())


async def login_input(e):
    uid = e.sender_id
    state = LOGINS.get(uid)
    if not state:
        return False
    if time.time()-state['created']>600:
        await close_login(uid)
        raise ValueError('Login timed out; /login again')
    raw = e.raw_text.strip()
    if state['step']=='phone':
        if not re.fullmatch(r'\+\d{7,15}',raw):
            raise ValueError('Phone must include country code: +91…')
        cl = TelegramClient(StringSession(),API_ID,API_HASH,flood_sleep_threshold=0)
        state['cl'] = cl
        state['phone'] = raw
        await cl.connect()
        sent = await cl.send_code_request(raw)
        state.update(hash=sent.phone_code_hash,step='code',code='')
        with contextlib.suppress(Exception):
            await e.delete()
        await respond(e,'OTP sent via '+type(sent.type).__name__+'. Enter digits using keypad; then Submit.',keypad())
    elif state['step']=='password':
        # Delete sensitive input before making requests, even on authentication failure.
        with contextlib.suppress(Exception):
            await e.delete()
        await state['cl'].sign_in(password=raw)
        await finish_login(e,state)
    elif state['step']=='code':
        if re.fullmatch(r'\d(?:[\s-]+\d){3,7}',raw):
            state['code'] = re.sub(r'\D','',raw)
            with contextlib.suppress(Exception):
                await e.delete()
            await submit_code(e,state)
        else:
            await respond(e,'Use inline keypad, or spaced digits. Plain OTP chat messages may expire.')
    return True


async def submit_code(e,state):
    if not 4<=len(state.get('code',''))<=8:
        raise ValueError('Enter all OTP digits first')
    try:
        await state['cl'].sign_in(phone=state['phone'],code=state['code'],phone_code_hash=state['hash'])
        await finish_login(e,state)
    except SessionPasswordNeededError:
        state['step'] = 'password'
        await respond(e,'Two-step verification enabled. Send your password here; input is deleted immediately. /cancellogin')


async def settings_view(e):
    cfg = cfg_for(e.sender_id)
    text = '⚙ Your isolated settings\n'+'\n'.join(f'{k}: {cfg[k]}' for k in ('target','sender','mode','workers','connections','delay','poll','filter','auto','caption','replacements'))
    await respond(e,text,[[Button.inline('Toggle auto-detect',b'set_auto'),Button.inline('Toggle sender',b'set_sender')],
                          [Button.inline('Cycle mode',b'set_mode'),Button.inline('Reset options',b'reset_menu')]])


async def watch_view(e,page=0):
    routes = DB.get(e.sender_id,'watch',[])
    text = '🔴 YOUR AUTOMATIC ROUTES\n━━━━━━━━━━━━━━━━━━━━\n\nNew messages use live events + polling catch-up.\nPause keeps the cursor; Resume copies backlog.\n'
    page=max(0,min(page,max(0,(len(routes)-1)//5)))
    text+=f'\nPage {page+1} • Routes: {len(routes)}\n'
    buttons = []
    for r in routes[page*5:(page+1)*5]:
        text += f"\n{'⏸ PAUSED' if r.get('paused') else '🟢 ACTIVE'} • {r['id']}\n\nSource: {r['src']}\nDestination: {r['target']}\nLast confirmed ID: {r.get('last')}\nStatus: {r.get('error') or 'OK'}\n━━━━━━━━━━━━━━━━━━━━\n"
        buttons.append([Button.inline('Resume' if r.get('paused') else 'Pause',('watch_toggle_'+r['id']).encode()),Button.inline('Retry now',('watch_retry_'+r['id']).encode()),Button.inline('Delete '+r['id'],('watch_del_'+r['id']).encode())])
    nav=[]
    if page:nav.append(Button.inline('← Previous',f'watchpage_{page-1}'.encode()))
    if (page+1)*5<len(routes):nav.append(Button.inline('Next →',f'watchpage_{page+1}'.encode()))
    if nav:buttons.append(nav)
    buttons.append([Button.inline('➕ Add route',b'menu_watch'),Button.inline('📖 Guide',b'help_watch')])
    await respond(e,text if routes else text+'No routes yet.',buttons)


async def get_topics(cl,peer):
    topics = []
    offset_date,offset_id,offset_topic = None,0,0
    seen = set()
    while True:
        result = await retry(cl,GetForumTopicsRequest(peer,offset_date,offset_id,offset_topic,100))
        batch = [t for t in result.topics if t.id not in seen]
        if not batch:
            break
        topics.extend(batch)
        seen.update(t.id for t in batch)
        if len(result.topics)<100:
            break
        last = result.topics[-1]
        offset_topic = last.id
        offset_id = last.top_message
        msg = next((m for m in result.messages if m.id==last.top_message),None)
        offset_date = getattr(msg,'date',None)
    return topics


async def group_transfer(e,arg):
    uid = e.sender_id
    bits = arg.split()
    if not bits:
        raise ValueError('/grouptransfer <source> [max messages 1–10000]')
    count = int(bits[1]) if len(bits)>1 else 10000
    if not 1<=count<=10000:
        raise ValueError('Limit must be 1–10000')
    cfg = cfg_for(uid)
    cl = await fetcher(uid,source_ref(bits[0]))
    src = await entity(cl,source_ref(bits[0]))
    sender,dest,topic = await destination_entity(uid,cfg)
    if utils.get_peer_id(src)==utils.get_peer_id(dest):
        raise ValueError('Source equals destination')
    await respond(e,f'Scanning up to {count} newest messages; creating/reusing real destination topics when supported…')
    mapping = {}
    if getattr(src,'forum',False) and getattr(dest,'forum',False):
        source_topics = await get_topics(cl,src)
        dest_topics = await get_topics(sender,dest)
        titles = {getattr(t,'title',''):t.id for t in dest_topics}
        for t in source_topics:
            if t.id == 1:
                mapping['1'] = None
                continue
            title = getattr(t,'title','Topic '+str(t.id))
            if title not in titles:
                result = await retry(sender,CreateForumTopicRequest(dest,title))
                created = next((u.message.id for u in result.updates if getattr(getattr(u,'message',None),'action',None) and type(u.message.action).__name__=='MessageActionTopicCreate'),None)
                if not created:
                    raise ValueError('Telegram did not return created forum topic')
                titles[title] = created
            mapping[str(t.id)] = titles[title]
    elif getattr(src,'forum',False):
        cfg['caption'] = '[{index}] '+(cfg.get('caption') or '{original}')
    messages = await retry(cl.get_messages,src,limit=count)
    links = [(str(utils.get_peer_id(src)),m.id) for m in reversed(messages) if not m.action]
    await start_job(e,links,cfg,topic_map=mapping)


async def command(e,cmd,arg):
    uid = e.sender_id
    if cmd in ('start','help'):
        text,buttons = help_page(arg or 'home',owner=uid==OWNER_ID)
        if not CHECKPOINTS and os.getenv('PERSISTENT_STORAGE')!='1' and (arg or 'home')=='home':
            text += '\n\n💾 Free hosting: restart/redeploy may erase local state. Owner: /backup before changes; /help tools for recovery details.'
        if CHECKPOINTS and (arg or 'home')=='home':
            text += '\n\n🔐 Saved logins: encrypted remote recovery enabled. Temporary disconnects reconnect automatically. Telegram-revoked sessions still require /login.'
        return await respond(e,text,buttons)
    if cmd == 'support':
        return await respond(e,'🆘 SUPPORT\n━━━━━━━━━━━━━━━━━━━━\n\nBot mein problem? Contact @SyntaxError_2.\n\nSaath bhejein: command/route ID, error screenshot aur approximate time.\n\nOTP, password, StringSession ya backup encryption key kabhi share na karein.',support_buttons())
    if cmd == 'id':
        reply = await e.get_reply_message() if e.is_reply else e.message
        tid = topic_id(reply)
        return await respond(e,f'Chat ID: {e.chat_id}\nYour ID: {uid}\nTopic ID: {tid if getattr(getattr(reply,"reply_to",None),"forum_topic",False) else "General / no topic"}\nMessage ID: {reply.id}')
    if cmd == 'login':
        started=await start_login(e)
        if arg and started:
            e.message.message = arg
            await login_input(e)
        return
    if cmd == 'reconnect':
        saved=DB.get(uid,'session')
        if not saved:
            raise ValueError('No saved session; /login first')
        await ensure_session(uid,force=True)
        return await respond(e,'✅ Your saved session reconnected. /watchlist to check restored routes.')
    if cmd == 'cancellogin':
        await close_login(uid)
        return await respond(e,'Login cancelled.')
    if cmd == 'logout':
        if DB.get(uid,'session') and uid not in USER_CLIENTS:
            try:
                await ensure_session(uid,force=True)
            except LoginRequired:
                pass
        await disconnect_user(uid,revoke=True)
        DB.set(uid,'session','')
        if RECOVERY:RECOVERY.forget(uid)
        await durable_flush(critical=True)
        await close_login(uid)
        return await respond(e,'Your Telegram session was logged out. Watch routes remain paused until you /login.')
    if cmd == 'session':
        if not arg:
            raise ValueError('/session <StringSession> (private chat only)')
        with contextlib.suppress(Exception):
            await e.delete()
        await connect_user(uid,arg)
        synced=await durable_flush(critical=True)
        return await respond(e,'✅ Your session imported and encrypted.'+(' Remote recovery saved.' if CHECKPOINTS and synced else ' Remote recovery pending/unconfigured.'))
    if cmd == 'exportsession':
        session = DB.get(uid,'session')
        if not session:
            raise ValueError('No session to export')
        return await respond(e,'This exports full account access. Confirm only in your own private chat.',[[Button.inline('Export my session',b'export_confirm')]])
    if cmd in ('chats','dialogs'):
        cl = await ensure_session(uid)
        lines = []
        async for d in cl.iter_dialogs(limit=100):
            lines.append(f'{d.id}: {d.name}')
        buf = io.BytesIO('\n'.join(lines).encode())
        buf.name = 'my_chats.txt'
        return await BOT.send_file(uid,buf,caption='Your first 100 dialogs',parse_mode=None)
    if cmd == 'join':
        cl = await ensure_session(uid)
        invite = re.search(r't\.me/(?:\+|joinchat/)([\w-]+)',arg)
        result = await retry(cl,ImportChatInviteRequest(invite[1])) if invite else await retry(cl,JoinChannelRequest(source_ref(arg)))
        return await respond(e,'Join request processed. Approval may be required by the channel admin.')
    if cmd in ('forward','publicforward'):
        if cmd == 'forward':
            await ensure_session(uid)
        PANELS[uid] = dict(step='first',cfg=cfg_for(uid))
        return await respond(e,'Send FIRST post link. Then last link / ID. /cancel to exit.')
    if cmd == 'save':
        return await start_job(e,parse_links(arg))
    if cmd == 'batch':
        bits = arg.split()
        if len(bits)!=2:
            raise ValueError('/batch <first post link> <last ID or last link>')
        first = parse_post_link(bits[0])
        last = parse_post_link(bits[1])
        if not first or last and last[0]!=first[0]:
            raise ValueError('Use first/last links from the same source')
        end = last[1] if last else int(bits[1])
        a,b = sorted((first[1],end))
        if a<1 or b-a>=10000:
            raise ValueError('Maximum 10,000 posts')
        return await start_job(e,[(first[0],i) for i in range(a,b+1)])
    if cmd == 'grouptransfer':
        return await group_transfer(e,arg)
    if cmd == 'bottransfer':
        await ensure_session(uid)
        bits = arg.split()
        if not bits:
            raise ValueError('/bottransfer @bot [first ID] [last ID]')
        cl = USER_CLIENTS[uid]
        ent = await entity(cl,source_ref(bits[0]))
        if not getattr(ent,'bot',False):
            raise ValueError('Source is not a Telegram bot')
        latest = await cl.get_messages(ent,limit=1)
        first = int(bits[1]) if len(bits)>1 else 1
        last = int(bits[2]) if len(bits)>2 else (latest[0].id if latest else 1)
        if first<1 or last<first or last-first>=10000:
            raise ValueError('Choose positive IDs, up to 10,000 messages')
        return await start_job(e,[(str(ent.id),i) for i in range(first,last+1)])
    if cmd in ('cancel','stop'):
        control_ui.INPUT.pop(uid,None)
        if uid==OWNER_ID:
            control_ui.CONFIRM.clear()
        PANELS.pop(uid,None)
        await close_login(uid)
        await stop_job(uid)
        return await respond(e,'Your job/panel/login stopped. /resume keeps the last confirmed checkpoint. Live routes: /watchlist to pause.')
    if cmd == 'resume':
        pending = DB.get(uid,'pending_job')
        if not pending:
            raise ValueError('No pending job')
        saved_cfg = dict(pending.get('cfg') or {})
        for key in ('target','mode','caption'):
            if key in pending and key not in saved_cfg:
                saved_cfg[key] = pending[key]
        return await start_job(e,pending['links'],saved_cfg,pending.get('start',pending.get('idx',0)),pending.get('topic_map'))
    if cmd == 'discard':
        if uid in JOBS:
            raise ValueError('/stop before discarding')
        DB.set(uid,'pending_job',None)
        return await respond(e,'Your resume checkpoint deleted.')
    if cmd == 'watch':
        route = await add_watch(uid,arg)
        return await respond(e,'🔴 Route enabled: '+route['id']+'\nReal-time hook + polling catch-up. /watchlist')
    if cmd == 'watchlist':
        return await watch_view(e)
    if cmd == 'unwatch':
        routes = DB.get(uid,'watch',[])
        if not arg:
            return await watch_view(e)
        selected = [r for r in routes if arg=='all' or r['id']==arg or r['src']==arg]
        if not selected:
            raise ValueError('No matching route ID; /watchlist')
        DB.set(uid,'watch',[r for r in routes if r not in selected])
        for r in selected:
            t = WATCH_TASKS.get((uid,r['id']))
            if t:
                t.cancel()
        return await respond(e,f'Deleted {len(selected)} of your routes.')
    if cmd in ('settarget','setchannel'):
        cfg = cfg_for(uid)
        cfg.update(target=arg,topic=None)
        await destination_entity(uid,cfg)
        DB.set(uid,'target',arg)
        DB.set(uid,'topic',None)
        return await respond(e,'✅ Your destination verified and saved: '+arg)
    if cmd in ('setmode','setfilter','settypes','setworkers','setconnections','setdelay','setpoll'):
        key = cmd[3:]
        value = arg.strip()
        if key == 'mode' and value not in ('turbo','forward','download','copy'):
            raise ValueError('Use turbo, forward, download, copy')
        if key == 'filter' and value not in ('all','text'):
            raise ValueError('Use all or text')
        if key == 'types':
            key = 'type_filters'
            value = list(dict.fromkeys(re.split(r'[,\s]+',arg)))
            if arg == 'all':
                value = TYPES[:]
            if not value or any(v not in TYPES for v in value):
                raise ValueError('Types: '+', '.join(TYPES))
        if key in ('workers','connections'):
            value = int(value)
            maximum = 5 if key=='workers' else 6
            if not 1<=value<=maximum:
                raise ValueError(f'Choose 1–{maximum}')
        if key in ('delay','poll'):
            value = float(value)
            minimum = .1 if key=='delay' else 3
            if not minimum<=value<=3600:
                raise ValueError(f'Choose {minimum}–3600 seconds')
        DB.set(uid,key,value)
        return await respond(e,f'✅ Your {key}: {value}')
    if cmd in ('setcaption','clearcaption','clearreplace'):
        key = 'replacements' if cmd=='clearreplace' else 'caption'
        value = [] if cmd=='clearreplace' else (arg if cmd=='setcaption' else '')
        DB.set(uid,key,value)
        return await respond(e,'✅ Your '+key+' updated.')
    if cmd == 'replace':
        if '|' not in arg:
            raise ValueError('/replace old|new')
        old,new = arg.split('|',1)
        if not old:
            raise ValueError('Old text cannot be empty')
        rules = DB.get(uid,'replacements')
        rules.append([old,new])
        DB.set(uid,'replacements',rules)
        return await respond(e,'✅ Replacement added. /replaces to manage.')
    if cmd == 'replaces':
        rules = DB.get(uid,'replacements')
        buttons = [[Button.inline(f'Delete {i+1}',f'repl_{i}'.encode())] for i in range(len(rules))]
        return await respond(e,'Your replacements:\n'+('\n'.join(f'{i+1}. {a} → {b}' for i,(a,b) in enumerate(rules)) or 'None'),buttons)
    if cmd == 'settings':
        return await settings_view(e)
    if cmd == 'reset':
        return await respond(e,'Reset only YOUR settings, routes and pending job?',[[Button.inline('Keep my login',b'reset_keep'),Button.inline('Reset + logout',b'reset_logout')],[Button.inline('Cancel',b'reset_cancel')]])
    if cmd in ('status','stats','myplan','dyno_status'):
        cfg = cfg_for(uid)
        row = next((r for r in DB.users() if r[0]==uid),None)
        job = JOBS.get(uid)
        text = f"V{VERSION} | Your ID: {uid}\nLogged in: {uid in USER_CLIENTS}\nTarget: {cfg['target']}\nRoutes: {len(DB.get(uid,'watch',[]))}\nTotal messages sent: {row[5] if row else 0}\nYour job: {str(job['done'])+'/'+str(job['total']) if job else 'Idle'}\nPending resume: {bool(DB.get(uid,'pending_job'))}\nUptime: {int(time.time()-START)}s\nProcess peak RAM: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.1f} MiB\nWorkers: {cfg['workers']} | connections: {cfg['connections']}\nFile cap: {MAX_FILE//1048576} MiB (Telegram account limits also apply)\nNo paid plans configured. Server uses bounded shared capacity."
        text+='\n\nSession health: '+str(RECOVERY.status(uid)['state'] if RECOVERY else 'not monitored')+'\nStorage: '+storage_status()
        return await respond(e,text,support_buttons())
    if cmd == 'ping':
        t = time.monotonic()
        msg = await respond(e,'Pinging Telegram…')
        return await msg.edit(f'Telegram round-trip: {(time.monotonic()-t)*1000:.0f} ms\nUptime: {int(time.time()-START)}s',parse_mode=None)
    if cmd == 'cleanup_ram':
        removed = 0
        # Never delete another user's or an active task's downloads.
        if uid not in JOBS and not any(u==uid for u,r in WATCH_TASKS):
            for p in ROOT.glob(f'u_{uid}_*'):
                if p.is_dir():
                    removed += sum(x.stat().st_size for x in p.rglob('*') if x.is_file())
                    shutil.rmtree(p,ignore_errors=True)
        objects = gc.collect()
        return await respond(e,f'Your inactive temporary files removed: {removed} bytes\nGC reclaimed {objects} objects. Active transfers untouched.')
    if cmd in ADMIN_COMMANDS:
        return await admin(e,cmd,arg)
    await respond(e,'Unknown command. /help')


ADMIN_COMMANDS = {'admin','users','broadcast','ban','unban','approve','alljobs','access','backup','restore'}
COMMANDS = set('start help support id login reconnect cancellogin logout session exportsession chats dialogs join forward publicforward save batch grouptransfer bottransfer cancel stop resume discard watch watchlist unwatch settarget setchannel setmode setfilter settypes setworkers setconnections setdelay setpoll setcaption clearcaption clearreplace replace replaces settings reset status stats myplan dyno_status ping cleanup_ram'.split()) | ADMIN_COMMANDS


async def admin(e,cmd,arg):
    uid = e.sender_id
    if uid != OWNER_ID:
        raise ValueError('Owner-only administration. Your own forwarding tools are open to all users.')
    module = sys.modules[__name__]
    if cmd == 'admin':
        return await control_ui.home(module,e)
    if cmd == 'users':
        return await control_ui.users(module,e)
    if cmd == 'backup':
        return await control_ui.backup(module,e)
    if cmd == 'restore':
        return await control_ui.restore_preview(module,e)
    if cmd == 'userexport':
        text = '\n'.join(f'{u} | {name} | @{username} | banned={banned} | {role} | sent={n}' for u,name,username,banned,role,n in DB.users()) or 'No users'
        file = io.BytesIO(text.encode())
        file.name = 'users.txt'
        return await BOT.send_file(uid,file,parse_mode=None)
    if cmd == 'alljobs':
        return await control_ui.listing(module,e,'jobs')
    if cmd in ('ban','unban','approve'):
        target = int(arg)
        if target == OWNER_ID:
            raise ValueError('Cannot modify owner role')
        if not any(r[0]==target for r in DB.users()):
            raise ValueError('User must /start the bot first')
        DB.flag(target,'role' if cmd=='approve' else 'banned','approved' if cmd=='approve' else int(cmd=='ban'))
        if cmd == 'ban':
            await disconnect_user(target)
            await close_login(target)
        return await respond(e,cmd+' applied to '+str(target))
    if cmd == 'access':
        if arg not in ('open','whitelist'):
            raise ValueError('/access open|whitelist')
        DB.global_set('access',arg)
        # Stop active transfers immediately when access is revoked.
        for target in set(USER_CLIENTS) | set(JOBS) | set(LOGINS):
            if not DB.allowed(target):
                await disconnect_user(target)
                await close_login(target)
        return await respond(e,'Access mode: '+arg)
    if cmd == 'broadcast':
        if not arg:
            raise ValueError('/broadcast <text>')
        if DB.global_get('broadcast_active',False):
            raise ValueError('A broadcast is already running')
        DB.global_set('broadcast_active',True)
        async def send_all():
            ok,fail = 0,0
            try:
                for user,*_ in DB.users():
                    try:
                        await retry(BOT.send_message,user,arg,parse_mode=None)
                        ok += 1
                    except Exception:
                        fail += 1
                    await asyncio.sleep(.1)
                await notify(uid,f'Broadcast complete: {ok} delivered, {fail} failed.')
            finally:
                DB.global_set('broadcast_active',False)
        spawn(send_all())
        return await respond(e,'Broadcast started; final delivery counts will follow.')


async def callbacks(e):
    uid = e.sender_id
    data = e.data.decode()
    control_ui.INPUT.pop(uid,None)
    if uid==OWNER_ID and not data.startswith('adm_confirm_'):
        control_ui.CONFIRM.clear()
    if data.startswith('watchpage_'):
        return await watch_view(e,int(data[10:]))
    if data.startswith('help_'):
        text,buttons=help_page(data[5:],owner=uid==OWNER_ID)
        return await respond(e,text,buttons)
    if data=='nav_home':
        return await respond(e,f'⚡ TURBO FORWARDER V{VERSION}\n\nSelect your next action below.\n\n/help — detailed category-wise command guide',menu())
    if data.startswith('adm_'):
        return await control_ui.callback(sys.modules[__name__],e,data)
    if data.startswith('menu_'):
        action = data[5:]
        if action == 'watch':
            PANELS[uid] = dict(step='watch')
            return await respond(e,'Send source and optional destination:\n@source @dest:topic\nA post link or -100… ID also works. /cancel')
        return await command(e,{'login':'login','forward':'forward','settings':'settings','status':'status','help':'help','support':'support'}.get(action,'help'),'')
    if data == 'stopjob':
        await stop_job(uid)
        return await respond(e,'Your transfer stopped; /resume available.')
    if data == 'save_private':
        DB.set(uid,'target',str(uid))
        return await respond(e,'Your private destination saved.')
    if data.startswith('lg_'):
        st = LOGINS.get(uid)
        if not st:
            raise ValueError('No active login. /login')
        action = data[3:]
        if action == 'cancel':
            await close_login(uid)
            return await respond(e,'Login cancelled.')
        if st['step'] != 'code':
            raise ValueError('Keypad is not active for this login step')
        if time.time()-st['created']>600:
            await close_login(uid)
            raise ValueError('Login expired; /login again')
        if action.startswith('digit_'):
            st['code'] = (st.get('code','')+action[-1])[:8]
        elif action == 'back':
            st['code'] = st.get('code','')[:-1]
        elif action == 'submit':
            return await submit_code(e,st)
        return await respond(e,'OTP digits entered: '+'•'*len(st.get('code',''))+'\nPress Submit when complete.',keypad())
    if data == 'export_confirm':
        session = DB.get(uid,'session')
        if not session:
            raise ValueError('No saved session')
        buf = io.BytesIO(session.encode())
        buf.name = 'my_session.txt'
        await BOT.send_file(uid,buf,caption='Private session backup: gives full account access. Keep secret.',parse_mode=None)
        return await respond(e,'Your private session backup sent.')
    if data.startswith('pn_'):
        st = PANELS.get(uid)
        if not st or 'cfg' not in st:
            raise ValueError('Panel expired. /forward')
        action = data[3:]
        if action == 'cancel':
            PANELS.pop(uid,None)
            return await respond(e,'Panel cancelled.')
        if action == 'save':
            cfg = st['cfg']
            links = [(st['src'],i) for i in range(st['first'],st['last']+1)]
            await start_job(e,links,cfg)
            PANELS.pop(uid,None)
            return
        if action == 'sender':
            st['cfg']['sender'] = 'User' if st['cfg']['sender']=='Bot' else 'Bot'
            return await show_panel(e,st)
        if action not in ('promo','caption','rename','hyperlink','thumb','topic'):
            raise ValueError('Unknown panel action')
        st['step'] = 'thumb' if action=='thumb' else 'set_'+action
        hints = dict(rename='Use old|new (caption AND filename) or a new filename template with {index}.',caption='Use {original}, {index}, {title}.',hyperlink='Use Label|https://example.com or all|https://example.com.',thumb='Send a photo/JPEG. It will be resized locally.',topic='Send a numeric destination forum topic ID; 0 for General.')
        return await respond(e,'Send '+action+'. '+hints.get(action,'')+'\nSend skip to clear.')
    if data.startswith('set_'):
        key = data[4:]
        if key == 'auto':
            DB.set(uid,'auto',not DB.get(uid,'auto'))
        elif key == 'sender':
            value = 'User' if DB.get(uid,'sender')=='Bot' else 'Bot'
            if value=='User':
                await ensure_session(uid)
            DB.set(uid,'sender',value)
        elif key == 'mode':
            modes = ['turbo','forward','download','copy']
            DB.set(uid,'mode',modes[(modes.index(DB.get(uid,'mode'))+1)%4])
        else:
            raise ValueError('Unknown setting')
        return await settings_view(e)
    if data.startswith('reset_'):
        action = data[6:]
        if action == 'menu':
            return await command(e,'reset','')
        if action == 'cancel':
            return await respond(e,'Reset cancelled.')
        if action not in ('keep','logout'):
            raise ValueError('Unknown reset action')
        await stop_job(uid)
        for (u,r),t in list(WATCH_TASKS.items()):
            if u==uid:
                t.cancel()
                await asyncio.gather(t,return_exceptions=True)
        if action == 'logout':
            await disconnect_user(uid,revoke=True)
        await close_login(uid)
        DB.reset(uid,keep=action=='keep')
        if RECOVERY and action=='logout':RECOVERY.forget(uid)
        await durable_flush(critical=True)
        PANELS.pop(uid,None)
        shutil.rmtree(ROOT/f'u_{uid}',ignore_errors=True)
        return await respond(e,'Your settings, routes and checkpoint reset. Login '+('kept.' if action=='keep' else 'logged out.'))
    if data.startswith('watch_'):
        _,action,rid = data.split('_',2)
        routes = DB.get(uid,'watch',[])
        route = next((r for r in routes if r['id']==rid),None)
        if not route:
            raise ValueError('Route no longer exists')
        if action in ('retry','toggle') and route.get('flood_until',0)>time.time():
            raise ValueError(f'Telegram rate limit: wait {int(route["flood_until"]-time.time())+1}s before retry/resume')
        task = WATCH_TASKS.get((uid,rid))
        if task:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        if action == 'del':
            routes.remove(route)
        elif action == 'toggle':
            route['paused'] = not route.get('paused')
            if not route['paused']:
                await ensure_session(uid)
                route['retry_at']=0
        elif action == 'retry':
            if route.get('paused'):raise ValueError('Resume this route first')
            await ensure_session(uid)
            route['retry_at']=0
        else:
            raise ValueError('Unknown route action')
        DB.set(uid,'watch',routes)
        if action!='del' and not route.get('paused'):kick_watch(uid,rid,force=True)
        return await watch_view(e)
    if data.startswith('repl_'):
        rules = DB.get(uid,'replacements')
        i = int(data[5:])
        if 0<=i<len(rules):
            rules.pop(i)
            DB.set(uid,'replacements',rules)
        return await command(e,'replaces','')
    if data == 'admin_access':
        if uid != OWNER_ID:
            raise ValueError('Owner only')
        return await control_ui.ask(sys.modules[__name__],e,'whitelist' if DB.global_get('access','open')=='open' else 'open')
    raise ValueError('This button is from an old version. Use /start for the current menu.')


async def dispatch(e):
    uid = e.sender_id
    if not uid:
        return
    is_callback = isinstance(e,events.CallbackQuery.Event)
    raw = (e.raw_text or '') if not is_callback else ''
    match = re.match(r'^/(\w+)(?:@(\w+))?(?:\s+([\s\S]*))?$',raw.strip()) if not is_callback else None
    if match and match[2] and match[2].lower()!=USERNAME.lower():
        return
    # All sensitive commands and callbacks must stay in the requesting user's DM.
    if not e.is_private and not (match and match[1].lower()=='id'):
        if match:
            await respond(e,'Open my private chat for forwarding, login and settings. /id works here.')
        return
    try:
        sender = await e.get_sender()
        DB.touch(uid,getattr(sender,'first_name','') or '',getattr(sender,'username','') or '')
        if not DB.allowed(uid):
            return await respond(e,'Access disabled or owner approval required. Your data remains isolated.')
        if is_callback:
            with contextlib.suppress(Exception):
                await e.answer()
            return await callbacks(e)
        if match:
            control_ui.INPUT.pop(uid,None)
            if uid==OWNER_ID:
                control_ui.CONFIRM.clear()
            return await command(e,match[1].lower(),(match[3] or '').strip())
        # A sensitive login flow takes precedence over owner text-input prompts.
        if await login_input(e):
            return
        if await control_ui.handle_input(sys.modules[__name__],e):
            return
        if await panel_input(e):
            return
        if e.file and e.file.name and e.file.name.lower().endswith('.txt'):
            if e.file.size>512*1024:
                raise ValueError('TXT files must be <=512 KiB')
            data = await e.download_media(file=bytes)
            return await start_job(e,parse_links(data.decode('utf-8-sig')))
        if DB.get(uid,'auto'):
            links = parse_links(raw)
            if links:
                return await start_job(e,links)
    except AUTH_ERRORS as exc:
        if RECOVERY:RECOVERY.state[uid]={'state':'revoked','error':type(exc).__name__}
        await respond(e,'Telegram invalidated this session. /login again; settings/routes are retained. Support: @SyntaxError_2',support_buttons())
    except FloodWaitError as exc:
        await respond(e,f'Telegram rate limit: wait {exc.seconds}s, then retry. No speed setting can bypass it.',support_buttons())
    except Exception as exc:
        log.warning('Request uid=%s failed: %s',uid,type(exc).__name__)
        # Never echo raw request text: it may contain session/OTP/password.
        safe = str(exc)[:350] if isinstance(exc,(ValueError,IOError)) else type(exc).__name__
        await respond(e,'❌ '+safe+'\n\n/help · /cancel\nSupport: @SyntaxError_2',support_buttons())
    finally:
        mutations={'settarget','setchannel','watch','unwatch','setmode','setcaption','clearcaption','replace','clearreplace','setworkers','setconnections','setdelay','setpoll','setfilter','settypes','logout','session','access','ban','unban','approve','discard'}
        if CHECKPOINTS and ((match and match[1].lower() in mutations) or (is_callback and e.data.startswith((b'watch_',b'set_',b'repl_',b'reset_',b'adm_confirm_')))):
            await durable_flush(critical=True)


async def health(request):
    return web.json_response(dict(ok=bool(BOT and BOT.is_connected()),v=VERSION,uptime=int(time.time()-START),
                                  persistence='encrypted-checkpoints' if CHECKPOINTS else 'external' if os.getenv('PERSISTENT_STORAGE')=='1' else 'ephemeral',
                                  checkpoint_ok=not bool(CHECKPOINTS.remote.error) if CHECKPOINTS else False,
                                  checkpoint_age=int(time.time()-CHECKPOINTS.remote.last_success) if CHECKPOINTS and CHECKPOINTS.remote.last_success else None,
                                  session_encryption=bool(DB and DB.cipher)))


async def checkpoint_loop():
    while not STOPPING:
        await asyncio.sleep(max(30,int(os.getenv('STATE_SYNC_SECONDS','300'))))
        await durable_flush()


async def session_monitor():
    while not STOPPING:
        for uid,*_ in DB.users():
            if not DB.allowed(uid) or not DB.get(uid,'session'):
                continue
            try:
                await ensure_session(uid,optional=True)
            except (LoginRequired,SessionUnavailable):
                pass
        await asyncio.sleep(15)


async def transport_selftest():
    """Opt-in operator check: temporary file/message, byte-for-byte verification."""
    folder = ROOT/'transport_selftest'
    msg = None
    try:
        folder.mkdir(exist_ok=True)
        source = folder/'transport_check.bin'
        # Deterministic non-secret payload; 3 chunks (including uneven last chunk).
        source.write_bytes(bytes(range(256))*4096+b'v6-integrity')
        start = time.monotonic()
        uploaded = await Parallel(BOT,3).upload(str(source))
        msg = await BOT.send_file(OWNER_ID,uploaded,caption='Temporary V6 transport verification; removed automatically.',force_document=True)
        output = folder/'download.bin'
        await Parallel(BOT,3).download(msg,str(output))
        import hashlib
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(output.read_bytes()).digest():
            raise IOError('Transport hash mismatch')
        log.info('TRANSPORT_SELFTEST PASS: parallel upload/download, 3 connections, 512KiB chunks, SHA256 match; %.2fs',time.monotonic()-start)
    except Exception as exc:
        log.error('TRANSPORT_SELFTEST FAIL: %s: %s',type(exc).__name__,str(exc)[:160])
    finally:
        if msg:
            with contextlib.suppress(Exception):
                await BOT.delete_messages(OWNER_ID,[msg.id])
        shutil.rmtree(folder,ignore_errors=True)


async def maintenance():
    while not STOPPING:
        await asyncio.sleep(60)
        for uid,state in list(LOGINS.items()):
            if time.time()-state['created']>600:
                await close_login(uid)
        url = os.getenv('RENDER_EXTERNAL_URL')
        if url:
            with contextlib.suppress(Exception):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.get(url+'/health') as res:
                        await res.read()


async def migrate_routes(uid):
    raw = DB.get(uid,'watch',[])
    if not raw or all(isinstance(r,dict) and r.get('id') for r in raw):
        return
    # Resolve legacy references without silently dropping user routes.
    old_ids = DB.get(uid,'lastids',{})
    cl = USER_CLIENTS[uid]
    routes = []
    for old in raw:
        old = {'src':old} if isinstance(old,str) else old
        try:
            src = await entity(cl,source_ref(old['src']))
            src_id = str(utils.get_peer_id(src))
            latest = await cl.get_messages(src,limit=1)
            last = old_ids.get(old['src'],latest[0].id if latest else 0)
            target = old.get('target') or DB.get(uid,'target',str(uid))
            routes.append(dict(id=uuid.uuid4().hex[:8],src=src_id,target=target,last=int(last),paused=False,error=''))
        except Exception:
            # Preserve unresolved route visibly paused for manual repair.
            routes.append(dict(id=uuid.uuid4().hex[:8],src=old['src'],target=old.get('target') or str(uid),last=0,paused=True,error='Legacy source unresolved: delete and re-add route'))
    DB.set(uid,'watch',routes)


async def main():
    global BOT,DB,TRANSFER_LIMIT,USERNAME,STOPPING,CHECKPOINTS,RECOVERY
    if not API_ID or not API_HASH or not BOT_TOKEN:
        raise RuntimeError('API_ID, API_HASH and BOT_TOKEN are required')
    ROOT.mkdir(parents=True,exist_ok=True)
    # Only clean this application's stale transfer dirs, never persistent thumbnails.
    for p in ROOT.glob('u_*_*'):
        if p.is_dir():
            shutil.rmtree(p,ignore_errors=True)
    DB = Store(DB_PATH,OWNER_ID,os.getenv('SESSION_KEY'))
    DB.touch(OWNER_ID,'Owner','')
    if os.getenv('STATE_REPO_SSH'):
        remote=GitState(os.environ['STATE_REPO_SSH'],os.getenv('STATE_SSH_KEY'),os.getenv('STATE_KNOWN_HOSTS'),os.getenv('STATE_BRANCH','main'))
        CHECKPOINTS=Checkpoints(remote,DB,OWNER_ID,allow_initialize=False)
        restored=await CHECKPOINTS.recover_and_claim()
        log.info('Encrypted remote state %s; exclusive checkpoint writer claimed','restored' if restored else 'initialized')
    DB.global_set('broadcast_active',False)
    RECOVERY=SessionRecovery(DB,USER_CLIENTS,connect_user)
    TRANSFER_LIMIT = asyncio.Semaphore(int(os.getenv('GLOBAL_TRANSFERS','2')))
    bot_session = StringSession()
    if os.getenv('BOT_DC_ID') and os.getenv('BOT_DC_IP'):
        bot_session.set_dc(int(os.environ['BOT_DC_ID']),os.environ['BOT_DC_IP'],443)
    BOT = TelegramClient(bot_session,API_ID,API_HASH,flood_sleep_threshold=0)
    await BOT.start(bot_token=BOT_TOKEN)
    USERNAME = (await BOT.get_me()).username
    BOT.add_event_handler(dispatch,events.NewMessage(incoming=True))
    BOT.add_event_handler(dispatch,events.CallbackQuery())
    await BOT(SetBotCommandsRequest(BotCommandScopeDefault(),'', [BotCommand(a,b) for a,b in SLASH_MENU]))
    try:
        owner_peer=await BOT.get_input_entity(OWNER_ID)
        await BOT(SetBotCommandsRequest(BotCommandScopePeer(owner_peer),'', [BotCommand(a,b) for a,b in OWNER_MENU+SLASH_MENU]))
    except Exception as exc:
        log.warning('Owner slash menu setup: %s',type(exc).__name__)
    app = web.Application()
    app.router.add_get('/health',health)
    app.router.add_get('/',health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner,'0.0.0.0',int(os.getenv('PORT','8080'))).start()
    env_session = os.getenv('USER_SESSION')
    if env_session and not DB.get(OWNER_ID,'session'):
        DB.set(OWNER_ID,'session',env_session)
    # Restore saved sessions; failed logins remain stored for manual recovery.
    for uid,*_ in DB.users():
        if not DB.allowed(uid):
            continue
        try:
            saved = DB.get(uid,'session')
            if saved:
                await ensure_session(uid)
                await migrate_routes(uid)
        except Exception as exc:
            log.warning('Restore uid=%s: %s',uid,type(exc).__name__)
    # Legacy owner settings may exist even without a users row; owner is always registered.
    spawn(poll_loop())
    spawn(maintenance())
    spawn(session_monitor())
    if CHECKPOINTS:spawn(checkpoint_loop())
    if os.getenv('RUN_TRANSPORT_SELFTEST')=='1':
        spawn(transport_selftest())
    log.info('V%s ready @%s; access=%s; persistent=%s',VERSION,USERNAME,DB.global_get('access','open'),os.getenv('PERSISTENT_STORAGE','0'))
    try:
        await BOT.run_until_disconnected()
    finally:
        STOPPING = True
        for uid in list(JOBS):
            await stop_job(uid)
        for uid in list(USER_CLIENTS):
            await disconnect_user(uid)
        for uid in list(LOGINS):
            await close_login(uid)
        for task in list(BACKGROUND):
            task.cancel()
        await asyncio.gather(*list(BACKGROUND),return_exceptions=True)
        if CHECKPOINTS:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(durable_flush(critical=True),20)
            CHECKPOINTS.remote.close()
        await runner.cleanup()
        await BOT.disconnect()
        DB.close()


if __name__ == '__main__':
    asyncio.run(main())
