"""Small-deployment encrypted checkpoints over a PRIVATE Git repository.
One active writer. Not a transactional remote DB; non-critical rows sync periodically.
No general account token is needed at runtime: a repo-scoped SSH deploy key suffices.
"""
import asyncio
import contextlib
import os
from pathlib import Path
import re
import shlex
import signal
import tempfile
import time
import uuid

class StateConflict(RuntimeError):
    pass

class GitState:
    def __init__(self,url,key,known_hosts,branch='main',allow_local=False):
        if not allow_local and not re.fullmatch(r'git@github\.com:[\w.-]+/[\w.-]+\.git',url):
            raise ValueError('State storage must use the configured GitHub SSH repository')
        if not re.fullmatch(r'[\w.-]+',branch):
            raise ValueError('Invalid state branch')
        if not allow_local and (not key or not known_hosts):
            raise ValueError('State SSH key and pinned known_hosts are required')
        self.temp=tempfile.TemporaryDirectory(prefix='turbo-state-')
        self.root=Path(self.temp.name);self.repo=self.root/'repo'
        self.url=url;self.branch=branch;self.writer=uuid.uuid4().hex
        self.base=None;self.last_success=0;self.error='';self.ready=False
        self.env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GIT_CONFIG_NOSYSTEM='1')
        if key:
            private=self.root/'id_ed25519';private.write_text(key.strip()+'\n');private.chmod(0o600)
            hosts=self.root/'known_hosts';hosts.write_text(known_hosts.strip()+'\n');hosts.chmod(0o600)
            self.env['GIT_SSH_COMMAND']='ssh -i '+shlex.quote(str(private))+' -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile='+shlex.quote(str(hosts))+' -o ConnectTimeout=20'
    async def git(self,*args,cwd=None):
        proc=await asyncio.create_subprocess_exec('git','-c','credential.helper=',*args,cwd=str(cwd or self.repo),env=self.env,
                                                 stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
        try:
            stdout,stderr=await asyncio.wait_for(proc.communicate(),timeout=90)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGKILL)
            await proc.communicate()
            raise
        if proc.returncode:
            # Git stderr may contain remote/account details; do not expose it to Telegram/logs.
            raise IOError('State repository operation failed: '+args[0])
        return stdout
    async def open(self):
        await self.git('clone','--depth=1','--no-tags','--branch',self.branch,self.url,str(self.repo),cwd=self.root)
        await self.git('config','user.name','Turbo state checkpoint')
        await self.git('config','user.email','state@users.noreply.github.com')
        self.base=(await self.git('rev-parse','HEAD')).decode().strip()
        self.ready=True
    async def refresh(self):
        await self.git('fetch','--depth=1','origin',self.branch)
        await self.git('reset','--hard','origin/'+self.branch)
        self.base=(await self.git('rev-parse','HEAD')).decode().strip()
    async def load(self):
        if not self.ready:await self.open()
        else:await self.refresh()
        path=self.repo/'state.enc'
        if path.is_symlink():raise ValueError('Invalid checkpoint symlink')
        if not path.exists():
            if (self.repo/'writer').exists():
                raise IOError('Initialized state repository is missing its encrypted checkpoint')
            return None
        if path.stat().st_size>10*1024*1024:
            raise ValueError('Remote encrypted checkpoint exceeds 10 MiB')
        return path.read_bytes()
    async def write(self,blob):
        if not self.ready:raise IOError('State repository is not ready')
        if len(blob)>10*1024*1024:raise ValueError('State checkpoint too large')
        try:
            await self.git('fetch','--depth=1','origin',self.branch)
            remote=(await self.git('rev-parse','origin/'+self.branch)).decode().strip()
            head=(await self.git('rev-parse','HEAD')).decode().strip()
            if remote!=self.base:
                # A previous push may have succeeded although the network reply was lost.
                owner=(await self.git('show','origin/'+self.branch+':writer')).decode().strip() if remote==head else ''
                if remote!=head or owner!=self.writer:
                    raise StateConflict('Another instance wrote the remote checkpoint; refusing stale overwrite')
                self.base=remote
            await self.git('reset','--hard',self.base)
            if any((self.repo/name).is_symlink() for name in ('state.enc','writer')):
                raise ValueError('Invalid checkpoint path')
            (self.repo/'state.enc').write_bytes(blob)
            (self.repo/'writer').write_text(self.writer+'\n')
            await self.git('add','state.enc','writer')
            tree=(await self.git('write-tree')).decode().strip()
            commit=(await self.git('commit-tree',tree,'-m','Encrypted application checkpoint')).decode().strip()
            await self.git('update-ref','refs/heads/'+self.branch,commit)
            await self.git('push','--force-with-lease=refs/heads/'+self.branch+':'+self.base,'origin','HEAD:refs/heads/'+self.branch)
            self.base=(await self.git('rev-parse','HEAD')).decode().strip()
            self.error='';self.last_success=time.time()
        except Exception as exc:
            self.error=type(exc).__name__+': '+str(exc)[:160]
            raise
    def close(self):
        self.temp.cleanup()

class Checkpoints:
    def __init__(self,remote,store,owner,allow_initialize=True):
        self.allow_initialize=allow_initialize
        self.remote=remote;self.store=store;self.owner=owner
        self.lock=asyncio.Lock();self.revision=-1
    async def flush(self,force=False):
        async with self.lock:
            revision=self.store.db.total_changes
            if not force and revision==self.revision:return False
            blob=self.store.snapshot(self.owner)
            await self.remote.write(blob)
            self.revision=revision
            return True
    async def recover_and_claim(self):
        # Must run before Telegram handlers. Refuse to boot empty on an unavailable remote.
        for attempt in range(3):
            blob=await self.remote.load()
            if not blob and not self.allow_initialize:
                raise IOError('No remote checkpoint found; refusing to start empty. Seed a validated backup first.')
            if blob:
                data=self.store.decode_snapshot(blob,self.owner)
                self.store.restore_snapshot(data,self.owner)
            try:
                await self.flush(force=True)
                return bool(blob)
            except StateConflict:
                if attempt==2:raise
        raise StateConflict('Could not claim durable state')
