"""Bounded parallel MTProto transport + pure parsing/formatting helpers.
Telethon private transport APIs are version-pinned; standard API is safe fallback.
"""
import asyncio
import copy
import hashlib
import math
import os
import re
from pathlib import Path
from telethon import utils
from telethon.crypto import AuthKey
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.help import GetConfigRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import GetFileRequest, SaveFilePartRequest, SaveBigFilePartRequest
from telethon.tl.types import InputFile, InputFileBig
from telethon.errors import FloodWaitError

CHUNK = 512 * 1024
POST = re.compile(r'(?:https?://)?(?:www\.)?t\.me/(?:(c)/(\d+)|([A-Za-z][\w]{2,63}))/(\d+)(?:/(\d+))?(?:\s*[-–—]\s*(\d+))?', re.I)
TYPES = ['text','photo','video','document','audio','voice','animation','sticker','poll']

def parse_links(text, limit=10000):
    result = []
    for m in POST.finditer(text):
        private, number, public, first, topic_msg, end = m.groups()
        src = '-100'+number if private else public
        a = int(topic_msg or first)
        b = int(end or a)
        a,b = sorted((a,b))
        if a < 1 or b-a+1+len(result) > limit:
            raise ValueError('Maximum 10,000 posts; message IDs must be positive')
        result.extend((src,i) for i in range(a,b+1))
    return list(dict.fromkeys(result))

def parse_post_link(text):
    values = parse_links(text)
    return values[0] if values else None

def source_ref(raw):
    raw = str(raw).strip()
    parsed = parse_post_link(raw)
    if parsed:
        return parsed[0]
    m = re.fullmatch(r'(?:https?://)?t\.me/c/(\d+)/?',raw)
    if m:
        return '-100'+m[1]
    raw = re.sub(r'^(?:https?://)?t\.me/', '', raw).strip('/').lstrip('@')
    if re.fullmatch(r'-?\d+|[A-Za-z][\w]{2,63}',raw):
        return raw
    raise ValueError('Use @channel, -100… ID, or a Telegram post link')

def destination(raw):
    bits = str(raw).strip().rsplit(':',1)
    topic = None
    if len(bits) == 2 and bits[1].isdigit() and not bits[0].startswith('http'):
        raw, topic = bits[0], int(bits[1]) or None
    parsed = POST.search(str(raw))
    if parsed and parsed.group(5):
        topic = int(parsed.group(4))
    return source_ref(str(raw)), topic

def msg_type(m):
    for kind in ('sticker','voice','video_note','gif','video','audio','photo','poll','document'):
        if getattr(m,kind,None):
            return {'gif':'animation','video_note':'video'}.get(kind,kind)
    return 'text'

def topic_id(m):
    reply = getattr(m,'reply_to',None)
    if reply and getattr(reply,'forum_topic',False):
        return getattr(reply,'reply_to_top_id',None) or getattr(reply,'reply_to_msg_id',None)
    return 1

def renamed(name, rule, index):
    if not rule:
        return Path(name).name
    if '|' in rule:
        old,new = rule.split('|',1)
        name = name.replace(old,new) if old else name
    else:
        name = rule.replace('{index}',str(index)).replace('{filename}',Path(name).stem)+Path(name).suffix
    name = re.sub(r'[\\/\x00-\x1f]', '_',name)
    return (name[:220] or 'file')

async def retry(fn,*args,**kw):
    for attempt in range(4):
        try:
            return await fn(*args,**kw)
        except FloodWaitError as exc:
            if attempt == 3:
                raise
            await asyncio.sleep(exc.seconds+1)

class Parallel:
    def __init__(self,client,connections=4):
        self.client = client
        self.n = max(1,min(6,int(connections)))
        self.senders = []
    async def open(self,dc_id):
        c = self.client
        dc_id = dc_id or c.session.dc_id
        dc = await c._get_dc(dc_id)
        async def connect_one():
            same=dc_id==c.session.dc_id
            key=AuthKey(c.session.auth_key.key) if same else None
            sender=MTProtoSender(key,loggers=c._log,retries=2)
            self.senders.append(sender)
            await sender.connect(c._connection(dc.ip_address,dc.port,dc.id,loggers=c._log,proxy=c._proxy,local_addr=c._local_addr))
            init=copy.copy(c._init_request)
            if same:init.query=GetConfigRequest()
            else:
                auth=await retry(c,ExportAuthorizationRequest(dc_id))
                init.query=ImportAuthorizationRequest(auth.id,auth.bytes)
            await sender.send(InvokeWithLayerRequest(LAYER,init))
        tasks=[asyncio.create_task(connect_one()) for _ in range(self.n)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await self.close()
            raise
    async def close(self):
        await asyncio.gather(*(s.disconnect() for s in self.senders),return_exceptions=True)
        self.senders.clear()
    async def _workers(self, total, fn):
        async def worker(i,sender):
            for part in range(i,total,len(self.senders)):
                await fn(sender,part)
        tasks = [asyncio.create_task(worker(i,s)) for i,s in enumerate(self.senders)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    async def download(self,message,path,progress=None):
        info = utils._get_file_info(message.media)
        size = message.file.size
        await self.open(info.dc_id)
        total = math.ceil(size/CHUNK)
        done = 0
        try:
            with open(path,'w+b') as f:
                f.truncate(size)
                async def part(sender,i):
                    nonlocal done
                    result = await retry(sender.send,GetFileRequest(info.location,i*CHUNK,CHUNK))
                    data = getattr(result,'bytes',None)
                    expected = min(CHUNK,size-i*CHUNK)
                    if data is None or len(data) != expected:
                        raise IOError('CDN redirect or incomplete MTProto part; use standard fallback')
                    f.seek(i*CHUNK)
                    f.write(data)
                    done += len(data)
                    if progress:
                        progress(done,size)
                await self._workers(total,part)
            return path
        finally:
            await self.close()
    async def upload(self,path,progress=None):
        size = os.path.getsize(path)
        total = math.ceil(size/CHUNK)
        big = size > 10*1024*1024
        file_id = int.from_bytes(os.urandom(8),'little',signed=True)
        await self.open(self.client.session.dc_id)
        done = 0
        try:
            with open(path,'rb') as f:
                async def part(sender,i):
                    nonlocal done
                    f.seek(i*CHUNK)
                    data = f.read(CHUNK)
                    req = SaveBigFilePartRequest(file_id,i,total,data) if big else SaveFilePartRequest(file_id,i,data)
                    if not await retry(sender.send,req):
                        raise IOError('Telegram rejected uploaded part')
                    done += len(data)
                    if progress:
                        progress(done,size)
                await self._workers(total,part)
            if big:
                return InputFileBig(file_id,total,Path(path).name)
            digest = hashlib.md5()
            with open(path,'rb') as f:
                for chunk in iter(lambda:f.read(CHUNK),b''):
                    digest.update(chunk)
            return InputFile(file_id,total,Path(path).name,digest.hexdigest())
        finally:
            await self.close()
