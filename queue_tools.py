"""Non-fatal item handling and bounded prefetch disk admission."""
import asyncio
import shutil
import time
from pathlib import Path

class ItemUnavailable(ValueError):
    """A content-specific failure; record it and continue, do not hide global errors."""

class ResourceLimit(ValueError):
    """Host-wide capacity/configuration issue; pause instead of discarding videos."""

ITEM_ERROR_NAMES={'MediaEmptyError','MediaInvalidError','MessageIdInvalidError','MessageEmptyError',
                  'PhotoInvalidError','PhotoInvalidDimensionsError','VideoContentTypeError','MessageTooLongError'}
def nonfatal(exc):
    return isinstance(exc,ItemUnavailable) or type(exc).__name__ in ITEM_ERROR_NAMES

def poll_text(message):
    media=getattr(message,'poll',None)
    poll=getattr(media,'poll',media)
    question=getattr(poll,'question','')
    question=getattr(question,'text',question) or 'Poll'
    quiz=bool(getattr(poll,'quiz',False))
    text=('🗳 QUIZ' if quiz else '🗳 POLL')+' — text archive (not interactive)\n\n'+str(question)
    for i,answer in enumerate(getattr(poll,'answers',[]) or [],1):
        label=getattr(answer,'text','')
        text+=f'\n{i}. {getattr(label,"text",label)}'
    results=getattr(media,'results',None)
    count=getattr(results,'total_voters',None)
    if count is not None:text+=f'\n\nSource votes at copy time: {count}'
    text+='\n\nOriginal votes/hidden quiz answers are not cloned.'
    return text

class DiskBudget:
    """Reserve entire preparation units before download; wait OUTSIDE transport slots."""
    def __init__(self,root,capacity,margin=64*1024*1024,timeout=600):
        self.root=Path(root);self.capacity=capacity;self.margin=margin;self.timeout=timeout
        self.reserved={};self.condition=asyncio.Condition()
    def actual(self):
        total=0
        for folder in self.reserved:
            for path in Path(folder).rglob('*'):
                try:
                    if path.is_file():total+=path.stat().st_size
                except FileNotFoundError:pass
        return total
    async def reserve(self,folder,size):
        key=str(Path(folder));size=max(1,int(size))
        if size>self.capacity:
            raise ResourceLimit('This file/album exceeds the temporary-disk budget. Use allowed server-side copying, a smaller batch/album, or larger hosting storage.')
        started=time.monotonic()
        async with self.condition:
            while key not in self.reserved:
                physical=self.actual()
                capacity=min(self.capacity,shutil.disk_usage(self.root).free+physical-self.margin)
                if sum(self.reserved.values())+size<=capacity:
                    self.reserved[key]=size
                    return
                if time.monotonic()-started>self.timeout:
                    raise ResourceLimit('Waiting for temporary-disk space timed out; transfer paused without discarding this item.')
                try:await asyncio.wait_for(self.condition.wait(),2)
                except asyncio.TimeoutError:pass
    async def release_tree(self,folder):
        root=Path(folder)
        async with self.condition:
            for key in list(self.reserved):
                if Path(key)==root or root in Path(key).parents:self.reserved.pop(key,None)
            self.condition.notify_all()

def text_chunks(text,max_units=3500):
    """Safe plain-text chunks, including astral Unicode; no misleading truncation."""
    parts=[];buffer=[];units=0
    for char in text:
        width=2 if ord(char)>0xFFFF else 1
        if buffer and units+width>max_units:
            parts.append(''.join(buffer));buffer=[];units=0
        buffer.append(char);units+=width
    if buffer:parts.append(''.join(buffer))
    return parts
