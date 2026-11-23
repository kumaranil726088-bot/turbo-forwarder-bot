"""Original-media extraction with atomic files and safe MTProto fallback.
No transcoding or permission bypass. A denied/empty download remains a visible error.
"""
import asyncio
from pathlib import Path
from telethon.errors import FileReferenceExpiredError, FileReferenceInvalidError, FloodWaitError
from transfer import Parallel,retry

async def download_original(client,message,path,connections=4):
    path=Path(path);partial=path.with_name(path.name+'.partial')
    current=message
    for attempt in range(3):
        try:
            partial.unlink(missing_ok=True)
            size=current.file.size or 0
            try:
                if size<1024*1024:
                    raise ValueError('Small media uses standard transfer')
                await Parallel(client,connections).download(current,str(partial))
            except (asyncio.CancelledError,FloodWaitError,FileReferenceExpiredError,FileReferenceInvalidError):
                raise
            except Exception:
                # A failed parallel attempt may have preallocated a correctly-sized ZERO file.
                # Delete it before fallback and require the standard API to return a real file.
                partial.unlink(missing_ok=True)
                result=await retry(client.download_media,current,file=str(partial))
                if not result:
                    raise IOError('Telegram returned no downloadable media; access/permissions may prevent extraction')
            if not partial.is_file() or not partial.stat().st_size or (size and partial.stat().st_size!=size):
                raise IOError('Media is incomplete; refusing to upload a partial file')
            partial.replace(path)
            return str(path)
        except (FileReferenceExpiredError,FileReferenceInvalidError):
            if attempt==2:raise
            fresh=await retry(client.get_messages,current.peer_id,ids=current.id)
            if not fresh or not fresh.file:
                raise ValueError('Source media was removed or is no longer accessible')
            current=fresh
        except (OSError,ConnectionError,asyncio.TimeoutError):
            if attempt==2:raise
            await asyncio.sleep(.5*2**attempt)
        finally:
            partial.unlink(missing_ok=True)
