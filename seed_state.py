"""Operator migration: seed private encrypted recovery before first V6.2 deployment.
Use a .tbackup from /backup, or explicitly select --new-empty (fresh setup).
Runtime needs repo-scoped deploy key env vars, never a general GitHub token.
"""
import argparse
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from remote_state import GitState
from storage import Store

async def seed(path=None,new_empty=False,replace=False):
    owner=int(os.environ['OWNER_ID'])
    store=Store(':memory:',owner,os.environ['SESSION_KEY'])
    remote=GitState(os.environ['STATE_REPO_SSH'],os.environ['STATE_SSH_KEY'],os.environ['STATE_KNOWN_HOSTS'],os.getenv('STATE_BRANCH','main'))
    try:
        if path:
            file=Path(path)
            if file.stat().st_size>10*1024*1024:raise ValueError('Backup exceeds 10 MiB')
            data=store.decode_snapshot(file.read_bytes(),owner)
            store.restore_snapshot(data,owner)
        elif new_empty:
            store.touch(owner,'Owner','')
        else:
            raise ValueError('Supply a validated backup or explicitly choose --new-empty')
        existing=await remote.load()
        if existing and not replace:
            raise ValueError('Remote checkpoint already exists; refusing replacement. Use live /restore for normal recovery.')
        await remote.write(store.snapshot(owner))
        print('Validated encrypted checkpoint installed; users:',len(store.users()))
    finally:
        remote.close();store.close()

if __name__=='__main__':
    load_dotenv()
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--backup',help='Path to owner encrypted .tbackup document')
    group.add_argument('--new-empty',action='store_true',help='Explicit fresh setup; no old accounts/settings recovered')
    parser.add_argument('--replace',action='store_true',help='Only during a planned offline migration; overwrite existing checkpoint')
    args=parser.parse_args()
    asyncio.run(seed(args.backup,args.new_empty,args.replace))
