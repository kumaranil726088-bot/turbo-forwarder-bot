"""Owner-only moderation UI. No user impersonation or session-secret disclosure."""
import asyncio
import io
import time
from telethon import Button

INPUT = {}
CONFIRM = {}

def kb(label,data):
    return Button.inline(label,data.encode())

def require(b,e):
    if e.sender_id != b.OWNER_ID:
        raise ValueError('Owner-only control centre')

def user_row(b,uid):
    row=next((r for r in b.DB.users() if r[0]==uid),None)
    if not row:
        raise ValueError('User not found; user must /start first')
    return row

async def home(b,e):
    require(b,e)
    users=b.DB.users()
    routes=[r for u,*_ in users for r in b.DB.get(u,'watch',[])]
    text=(f'👑 OWNER CONTROL CENTRE • V{b.VERSION}\n━━━━━━━━━━━━━━━━━━━━\n\n'
          f'👥 Registered users: {len(users)}\n🚫 Banned users: {sum(bool(r[3]) for r in users)}\n'
          f'🔐 Connected sessions: {len(b.USER_CLIENTS)} / {b.MAX_SESSIONS}\n\n'
          f'⚡ Running jobs: {len(b.JOBS)}\n🔴 Live routes: {len(routes)}\n⏸ Paused routes: {sum(bool(r.get("paused")) for r in routes)}\n\n'
          f'🚪 Access mode: {b.DB.global_get("access","open").upper()}\n💾 Storage: {"persistent (operator configured)" if b.os.getenv("PERSISTENT_STORAGE")=="1" else "ephemeral — backup recommended"}\n\n'
          'Select a section below. Destructive actions require confirmation.\n\n'
          '🔒 Account credentials/OTP/session strings are not exposed here.')
    buttons=[[kb('👥 Manage users','adm_users_0'),kb('🔎 Find user ID','adm_find')],
             [kb('⚡ Jobs','adm_jobs_0'),kb('🔴 Live routes','adm_routes_0')],
             [kb('🚪 Access control','adm_access'),kb('📣 Broadcast','adm_broadcast')],
             [kb('💾 Encrypted backup','adm_backup'),kb('♻ Restore guide','adm_restore')],
             [kb('📖 Owner guide','help_admin'),kb('↻ Refresh','adm_home')]]
    return await b.respond(e,text,buttons)

async def users(b,e,page=0):
    require(b,e)
    rows=b.DB.users();page=max(0,min(page,max(0,(len(rows)-1)//8)))
    text=f'👥 USER MANAGEMENT\n━━━━━━━━━━━━━━━━━━━━\n\nPage {page+1} • {len(rows)} registered\n\n'
    buttons=[]
    for uid,name,username,banned,role,total in rows[page*8:(page+1)*8]:
        text+=f'{"🚫" if banned else "✅"} {uid} • {str(name)[:40]}\n   Role: {role} | Sent: {total}\n\n'
        buttons.append([kb(f'Manage {uid}',f'adm_user_{uid}')])
    nav=[]
    if page:nav.append(kb('← Previous',f'adm_users_{page-1}'))
    if (page+1)*8<len(rows):nav.append(kb('Next →',f'adm_users_{page+1}'))
    if nav:buttons.append(nav)
    buttons.extend([[kb('🔎 Find user','adm_find'),kb('📄 Export list','adm_export')],[kb('👑 Dashboard','adm_home')]])
    return await b.respond(e,text,buttons)

async def detail(b,e,uid):
    require(b,e)
    _,name,username,banned,role,total=user_row(b,uid)
    job=b.JOBS.get(uid);routes=b.DB.get(uid,'watch',[])
    text=(f'👤 USER DETAILS\n━━━━━━━━━━━━━━━━━━━━\n\nID: {uid}\nName: {str(name)[:80]}\nUsername: @{username or "—"}\n\n'
          f'Role: {role}\nBanned: {bool(banned)}\nAccess now: {b.DB.allowed(uid)}\nConnected: {uid in b.USER_CLIENTS}\n\n'
          f'Messages sent: {total}\nJob: {str(job["done"])+"/"+str(job["total"]) if job else "Idle"}\n'
          f'Resume checkpoint: {bool(b.DB.get(uid,"pending_job"))}\nRoutes: {len(routes)}\n\n'
          'Moderation affects this bot only, not the user’s Telegram account.')
    buttons=[]
    if uid!=b.OWNER_ID:
        buttons.append([kb('Unban' if banned else 'Ban',f'adm_ask_{"unban" if banned else "ban"}_{uid}'),kb('Approve whitelist',f'adm_ask_approve_{uid}')])
        if role=='approved':buttons.append([kb('Revoke whitelist approval',f'adm_ask_revoke_{uid}')])
    if job:buttons.append([kb('⏹ Stop transfer',f'adm_ask_stop_{uid}')])
    if routes:buttons.append([kb('⏸ Pause all routes',f'adm_ask_pause_{uid}'),kb('▶ Resume routes',f'adm_ask_resume_{uid}')])
    buttons.extend([[kb('↻ Refresh',f'adm_user_{uid}'),kb('👥 Users','adm_users_0')],[kb('👑 Dashboard','adm_home')]])
    return await b.respond(e,text,buttons)

async def listing(b,e,kind,page=0):
    require(b,e)
    entries=[]
    if kind=='jobs':
        entries=[(uid,j) for uid,j in list(b.JOBS.items())]
    else:
        entries=[(uid,r) for uid,*_ in b.DB.users() for r in b.DB.get(uid,'watch',[])]
    page=max(0,min(page,max(0,(len(entries)-1)//6)))
    text=f'{"⚡ ACTIVE JOBS" if kind=="jobs" else "🔴 ALL LIVE ROUTES"}\n━━━━━━━━━━━━━━━━━━━━\n\nPage {page+1} • Total: {len(entries)}\n\n'
    buttons=[]
    for uid,item in entries[page*6:(page+1)*6]:
        if kind=='jobs':
            text+=f'User: {uid}\nProgress: {item["done"]}/{item["total"]}\nSent: {item["ok"]} | {item["state"]}\n\n'
            buttons.append([kb(f'Stop {uid}',f'adm_ask_stop_{uid}'),kb('User',f'adm_user_{uid}')])
        else:
            rid=item['id']
            text+=f'User: {uid} • Route: {rid}\n{str(item["src"])[:70]} → {str(item["target"])[:70]}\n'
            text+=f'{"Paused" if item.get("paused") else "Active"} | Cursor: {item.get("last",0)}\nError: {str(item.get("error") or "None")[:180]}\n\n'
            action='playroute' if item.get('paused') else 'pauseroute'
            buttons.append([kb('Resume' if item.get('paused') else 'Pause',f'adm_ask_{action}_{uid}_{rid}'),kb('Delete',f'adm_ask_delroute_{uid}_{rid}')])
    if not entries:text+='Nothing here.\n'
    nav=[]
    if page:nav.append(kb('← Previous',f'adm_{kind}_{page-1}'))
    if (page+1)*6<len(entries):nav.append(kb('Next →',f'adm_{kind}_{page+1}'))
    if nav:buttons.append(nav)
    buttons.append([kb('↻ Refresh',f'adm_{kind}_{page}'),kb('👑 Dashboard','adm_home')])
    return await b.respond(e,text,buttons)

async def ask(b,e,action,uid=None,rid=None,payload=None):
    require(b,e)
    import secrets
    token=secrets.token_hex(6)
    CONFIRM.clear()  # one pending owner action; old buttons cannot trigger a changed request
    CONFIRM[token]=dict(action=action,uid=uid,rid=rid,payload=payload,expires=time.time()+180)
    descriptions={
      'ban':'Block this user and stop their active transfers. Disconnect session without revoking their Telegram account.',
      'unban':'Remove this bot ban. Whitelist approval remains separate.',
      'approve':'Approve this user for whitelist access.',
      'revoke':'Remove whitelist approval; when whitelist mode is enabled, active work is stopped.',
      'stop':'Stop this user’s transfer. Keep the resume checkpoint.',
      'pause':'Pause all this user’s routes, preserving cursors.',
      'resume':'Resume this user’s routes from saved cursors (backlog will be copied). User must have access and login.',
      'pauseroute':'Pause this route, preserving its cursor.',
      'playroute':'Resume this route from its saved cursor. Backlog may be copied.',
      'delroute':'Delete this route permanently. Re-adding starts from latest post.',
      'open':'Allow all registered, non-banned users to use the bot.',
      'whitelist':'Allow only approved users and owner. Stop active work for other users.',
      'broadcast':'Send the message below to all registered users.',
      'restore':'REPLACE database users/settings/routes/checkpoints with this backup. Disconnect all runtime sessions. Newer state will be overwritten; accounts need /reconnect after restore.'}
    text=f'⚠ CONFIRM ACTION\n━━━━━━━━━━━━━━━━━━━━\n\n{action.upper()}'+(f' • User {uid}' if uid else '')+(f' • Route {rid}' if rid else '')+'\n\n'+descriptions[action]
    if action=='broadcast':text+='\n\nMESSAGE PREVIEW\n\n'+str(payload)[:2500]
    if action=='restore':text+='\n\n'+str(payload['summary'])
    return await b.respond(e,text,[[kb('✅ Confirm',f'adm_confirm_{token}'),kb('✖ Cancel','adm_cancel')]])

async def pause_routes(b,uid,paused,rid=None,delete=False):
    routes=b.DB.get(uid,'watch',[])
    selected=[r for r in routes if rid is None or r['id']==rid]
    if not selected:raise ValueError('Route no longer exists')
    for r in selected:
        task=b.WATCH_TASKS.get((uid,r['id']))
        if task:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        r['paused']=paused
    b.DB.set(uid,'watch',[r for r in routes if r not in selected] if delete else routes)
    if not paused and not delete:
        for r in selected:b.kick_watch(uid,r['id'])

async def apply(b,e,token):
    require(b,e)
    state=CONFIRM.pop(token,None)
    if not state or time.time()>state['expires']:
        raise ValueError('Confirmation expired or already used; reopen panel')
    action,uid,rid,payload=(state[k] for k in ('action','uid','rid','payload'))
    if uid is not None:user_row(b,uid)
    if action in ('ban','unban','approve'):
        await b.admin(e,action,str(uid))
    elif action=='revoke':
        if uid==b.OWNER_ID:raise ValueError('Cannot change owner role')
        b.DB.flag(uid,'role','user')
        if not b.DB.allowed(uid):
            await b.disconnect_user(uid);await b.close_login(uid)
    elif action=='stop':await b.stop_job(uid)
    elif action in ('pause','resume','pauseroute','playroute','delroute'):
        if action in ('resume','playroute') and (not b.DB.allowed(uid) or uid not in b.USER_CLIENTS):
            raise ValueError('User must be allowed and logged in before route resume')
        await pause_routes(b,uid,action not in ('resume','playroute'),rid,delete=action=='delroute')
    elif action in ('open','whitelist'):
        await b.admin(e,'access',action)
    elif action=='broadcast':
        return await b.admin(e,'broadcast',payload)
    elif action=='restore':
        if b.DB.global_get('broadcast_active',False):
            raise ValueError('Wait for the current broadcast to finish before restoring')
        # Validate a second time before stopping any live work.
        b.DB.validate_snapshot(payload['data'],b.OWNER_ID)
        for u in list(b.JOBS):await b.stop_job(u)
        for u in list(b.USER_CLIENTS):await b.disconnect_user(u)
        for u in list(b.LOGINS):await b.close_login(u)
        b.PANELS.clear()
        b.DB.restore_snapshot(payload['data'],b.OWNER_ID)
        return await b.respond(e,'✅ Backup restored. Runtime sessions disconnected safely. Users: /reconnect to use their restored saved session (or /login if expired); saved checkpoints/routes are restored. /admin',[[kb('👑 Dashboard','adm_home')]])
    else:raise ValueError('Unknown admin action')
    if uid is not None:
        await b.notify(uid,'Bot owner moderation action: '+action+'. Your account credentials were not exposed.')
        return await detail(b,e,uid)
    return await home(b,e)

async def backup(b,e):
    require(b,e)
    data=b.DB.snapshot(b.OWNER_ID)
    file=io.BytesIO(data);file.name='turbo_backup_'+time.strftime('%Y%m%d_%H%M%S',time.gmtime())+'.tbackup'
    await b.BOT.send_file(b.OWNER_ID,file,parse_mode=None,caption='🔐 Encrypted database backup\nKeep this document + unchanged SESSION_KEY.\nRestore: reply to this file with /restore.\nContains encrypted account sessions/settings; never share.\nMedia files and custom thumbnail files are not included.')
    return await b.respond(e,'✅ Encrypted backup sent. Save it before any deploy/restart. This is manual recovery, not permanent hosting.',[[kb('👑 Dashboard','adm_home')]])

async def restore_preview(b,e):
    require(b,e)
    reply=await e.get_reply_message() if e.is_reply else None
    if not reply or not reply.file or not (reply.file.name or '').endswith('.tbackup'):
        raise ValueError('Reply to your .tbackup document with /restore. Existing state is replaced ONLY after confirmation.')
    if not reply.file.size or reply.file.size>10*1024*1024:
        raise ValueError('Backup must be <=10 MiB')
    encrypted=await reply.download_media(file=bytes)
    data=b.DB.decode_snapshot(encrypted,b.OWNER_ID)
    summary=f'Backup timestamp: {data["created"]}\nUsers: {len(data["users"])}\nSetting rows: {len(data["user_settings"])}'
    return await ask(b,e,'restore',payload=dict(data=data,summary=summary))

async def handle_input(b,e):
    if e.sender_id!=b.OWNER_ID:return False
    state=INPUT.pop(e.sender_id,None)
    if not state:return False
    if time.time()>state['expires']:raise ValueError('Admin input expired; reopen /admin')
    raw=(e.raw_text or '').strip()
    if state['kind']=='find':
        await detail(b,e,int(raw))
    elif state['kind']=='broadcast':
        if not raw or len(raw)>2500:raise ValueError('Broadcast message must be 1–2500 characters; reopen Broadcast to retry')
        await ask(b,e,'broadcast',payload=raw)
    return True

async def callback(b,e,data):
    require(b,e)
    parts=data.split('_');action=parts[1]
    if action=='home':return await home(b,e)
    if action=='users':return await users(b,e,int(parts[2]))
    if action=='user':return await detail(b,e,int(parts[2]))
    if action in ('jobs','routes'):return await listing(b,e,action,int(parts[2]))
    if action=='find' or action=='broadcast':
        if e.sender_id in b.LOGINS or e.sender_id in b.PANELS:
            raise ValueError('/cancel the current login/forward flow before entering admin text')
        INPUT[e.sender_id]=dict(kind=action,expires=time.time()+180)
        return await b.respond(e,'🔎 Send the registered user’s numeric ID. /cancel to exit.' if action=='find' else '📣 Send broadcast text (maximum 2500 characters). Preview + confirmation will follow. /cancel to exit.',[[kb('Cancel','adm_cancel')]])
    if action=='export':return await b.admin(e,'userexport','')
    if action=='access':
        return await b.respond(e,'🚪 ACCESS CONTROL\n━━━━━━━━━━━━━━━━━━━━\n\nCurrent: '+b.DB.global_get('access','open').upper()+'\n\nOPEN: all registered users except banned users.\n\nWHITELIST: only approved users and owner. Existing unauthorized work is stopped.',[[kb('🌐 Open mode','adm_ask_open'),kb('🔒 Whitelist','adm_ask_whitelist')],[kb('👑 Dashboard','adm_home')]])
    if action=='ask':return await ask(b,e,parts[2],int(parts[3]) if len(parts)>3 else None,parts[4] if len(parts)>4 else None)
    if action=='confirm':return await apply(b,e,parts[2])
    if action=='cancel':
        INPUT.pop(e.sender_id,None);CONFIRM.clear()
        return await home(b,e)
    if action=='backup':return await backup(b,e)
    if action=='restore':return await b.respond(e,'♻ RESTORE GUIDE\n\n1. Locate your encrypted .tbackup document.\n2. Reply to that file with /restore.\n3. Verify counts/timestamp.\n4. Confirm replacement.\n\nThe same SESSION_KEY and owner ID are required. Runtime sessions will disconnect; newer DB state is overwritten. Media/thumbnail files are not included.',[[kb('👑 Dashboard','adm_home')]])
    raise ValueError('Unknown owner panel action')
