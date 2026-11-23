"""Pure transfer planning and bounded per-run metadata cache."""
import asyncio
from collections import OrderedDict
from contextvars import ContextVar
import time

ACTIVE_METADATA=ContextVar('active_transfer_metadata',default=None)

class MetadataCache:
    def __init__(self,limit=128,ttl=30):
        self.limit=limit;self.ttl=ttl;self.values=OrderedDict();self.lock=asyncio.Lock()
    async def get(self,key,loader):
        async with self.lock:
            now=time.monotonic()
            hit=self.values.get(key)
            if hit and now-hit[0]<self.ttl:
                self.values.move_to_end(key)
                return hit[1]
            value=await loader()  # failed lookups are never cached
            self.values[key]=(time.monotonic(),value);self.values.move_to_end(key)
            while len(self.values)>self.limit:self.values.popitem(last=False)
            return value

def transfer_plan(client,sender,source,messages,cfg):
    if cfg.get('mode')=='download':return False,'Download mode explicitly selected'
    if getattr(source,'noforwards',False) or any(getattr(m,'noforwards',False) for m in messages):
        return False,'Source content protection is enabled; native copying unavailable'
    if cfg.get('rename') or cfg.get('thumb'):
        return False,'Filename/custom thumbnail changes require reupload'
    if client is not sender:return False,'Source account and sending account differ; choose User sender only if its destination is intended'
    return True,'Same-session server-side candidate; Telegram still checks access and file references'
