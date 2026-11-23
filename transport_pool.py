"""Bounded reusable MTProto connections. No credentials persisted here."""
import asyncio
from contextvars import ContextVar
import time

ACTIVE_POOL = ContextVar('active_transfer_pool', default=None)

class ConnectionPool:
    """Bounded exclusive leases; never share a connection between two transfers.

    Entries use exact client identity + DC + connection count keys; never user-supplied IDs.
    Any failed/cancelled transfer discards its lease. Idle sockets expire on acquire.
    """
    def __init__(self, capacity=2, idle_seconds=30):
        self.capacity=max(1,capacity);self.idle_seconds=idle_seconds
        self.entries=[];self.condition=asyncio.Condition();self.closed=False
        self.hits=0;self.misses=0

    async def acquire(self, client, dc, count, factory):
        key=(client,dc,count)
        async with self.condition:
            while True:
                if self.closed:raise RuntimeError('Transfer scope is closed')
                now=time.monotonic()
                stale=[e for e in self.entries if not e['busy'] and now-e['used']>self.idle_seconds]
                for e in stale:
                    self.entries.remove(e)
                    await e['transport'].close()
                for entry in self.entries:
                    if entry['key']==key and not entry['busy'] and not entry.get('retire'):
                        entry['busy']=True;self.hits+=1
                        return entry
                if len(self.entries)<self.capacity:break
                idle=[e for e in self.entries if not e['busy']]
                if idle:
                    old=min(idle,key=lambda e:e['used']);self.entries.remove(old)
                    await old['transport'].close()
                    break
                await self.condition.wait()
            entry=dict(key=key,busy=True,used=now,transport=factory())
            self.entries.append(entry);self.misses+=1
        try:
            await entry['transport'].open(dc)
            return entry
        except BaseException:
            await self.release(entry,discard=True)
            raise

    async def release(self, entry, discard=False):
        async with self.condition:
            if entry not in self.entries:return
            if discard or self.closed or entry.get('retire'):
                self.entries.remove(entry)
                await entry['transport'].close()
            else:
                entry.update(busy=False,used=time.monotonic())
            self.condition.notify_all()

    async def close_client(self, client):
        async with self.condition:
            for entry in list(self.entries):
                if entry['key'][0] is not client:continue
                if entry['busy']:
                    entry['retire']=True
                else:
                    self.entries.remove(entry)
                    await entry['transport'].close()
            self.condition.notify_all()

    async def close(self):
        # Caller joins transfer tasks first, so leased sockets are no longer in use.
        async with self.condition:
            self.closed=True
            entries,self.entries=self.entries,[]
            self.condition.notify_all()
        await asyncio.gather(*(e['transport'].close() for e in entries),return_exceptions=True)
