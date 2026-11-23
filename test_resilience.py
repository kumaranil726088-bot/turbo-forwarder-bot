"""Session recovery, checkpoint durability and watch regression tests."""
import asyncio
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.fernet import Fernet
from telethon.errors import FloodWaitError
import bot
from remote_state import GitState, Checkpoints, StateConflict
from sessions import SessionRecovery, LoginRequired, SessionUnavailable
from storage import Store
import test_v6 as fixtures
from test_v6 import Event

class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store=Store(':memory:',1,Fernet.generate_key());self.store.touch(1);self.store.touch(2)
        self.registry={};self.connector=AsyncMock();self.recovery=SessionRecovery(self.store,self.registry,self.connector)
    def tearDown(self):self.store.close()
    async def test_missing_login_only_prompts_once_needed(self):
        with self.assertRaises(LoginRequired):await self.recovery.ensure(2)
        self.connector.assert_not_awaited()
        self.assertIsNone(await self.recovery.ensure(2,optional=True))
    async def test_saved_session_recovered_without_otp(self):
        self.store.set(2,'session','saved-login')
        client=NS(is_connected=lambda:True)
        async def connect(uid,session):self.registry[uid]=client;return client
        self.connector.side_effect=connect
        for _ in range(3):self.assertIs(await self.recovery.ensure(2),client)
        self.connector.assert_awaited_once_with(2,'saved-login')
    async def test_concurrent_ensure_opens_one_client(self):
        self.store.set(2,'session','saved')
        client=NS(is_connected=lambda:True)
        async def connect(uid,session):
            await asyncio.sleep(.02);self.registry[uid]=client;return client
        self.connector.side_effect=connect
        await asyncio.gather(*(self.recovery.ensure(2) for _ in range(12)))
        self.assertEqual(self.connector.await_count,1)
    async def test_network_failure_keeps_saved_login(self):
        self.store.set(2,'session','saved');self.connector.side_effect=ConnectionError('offline')
        with self.assertRaises(SessionUnavailable):await self.recovery.ensure(2)
        self.assertEqual(self.store.get(2,'session'),'saved')
        with self.assertRaises(SessionUnavailable):await self.recovery.ensure(2)
        self.assertEqual(self.connector.await_count,1)
        self.assertEqual(self.recovery.status(2)['state'],'retrying')
    async def test_telegram_flood_wait_respected(self):
        self.store.set(2,'session','saved');self.connector.side_effect=FloodWaitError(None,capture=700)
        with self.assertRaises(SessionUnavailable):await self.recovery.ensure(2)
        self.assertGreater(self.recovery.status(2)['retry_at'],time.time()+699)
    async def test_revoked_session_distinguished_from_network(self):
        self.store.set(2,'session','saved');self.connector.side_effect=LoginRequired('revoked')
        with self.assertRaises(LoginRequired):await self.recovery.ensure(2)
        self.assertEqual(self.recovery.status(2)['state'],'revoked')
        self.assertEqual(self.store.get(2,'session'),'saved')
        self.assertIsNone(await self.recovery.ensure(2,optional=True))
    async def test_disconnected_client_reconnects_no_new_login(self):
        cl=NS(is_connected=lambda:False,connect=AsyncMock(),is_user_authorized=AsyncMock(return_value=True))
        self.registry[2]=cl
        self.assertIs(await self.recovery.ensure(2),cl)
        cl.connect.assert_awaited_once();self.connector.assert_not_awaited()
    async def test_own_session_only(self):
        self.store.set(1,'session','owner');self.store.set(2,'session','other')
        await self.recovery.ensure(2)
        self.connector.assert_awaited_once_with(2,'other')
    async def test_forced_revoked_connection_not_falsely_reused(self):
        cl=NS(is_connected=lambda:True,disconnect=AsyncMock());self.registry[2]=cl
        self.store.set(2,'session','saved');self.recovery.state[2]={'state':'revoked'}
        self.connector.side_effect=LoginRequired('expired')
        with self.assertRaises(LoginRequired):await self.recovery.ensure(2,force=True)
        cl.disconnect.assert_awaited_once();self.assertNotIn(2,self.registry)

class RepoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.origin=root/'origin.git';self.seed=root/'seed'
        def git(*args):return subprocess.run(['git',*map(str,args)],check=True,capture_output=True)
        git('init','--bare','--initial-branch=main',self.origin)
        git('clone',self.origin,self.seed)
        git('-C',self.seed,'config','user.name','Test');git('-C',self.seed,'config','user.email','test@example.com')
        (self.seed/'README.md').write_text('PRIVATE encrypted state test')
        git('-C',self.seed,'add','.');git('-C',self.seed,'commit','-m','Initialize')
        git('-C',self.seed,'push','origin','main')
        self.remotes=[];self.stores=[]
    async def asyncTearDown(self):
        for remote in self.remotes:remote.close()
        for store in self.stores:store.close()
        self.tmp.cleanup()
    def remote(self):
        remote=GitState(str(self.origin),None,None,allow_local=True);self.remotes.append(remote);return remote
    def store(self,key=None):
        store=Store(':memory:',1,key or Fernet.generate_key());store.touch(1);self.stores.append(store);return store
    async def test_fresh_restart_recovers_encrypted_session(self):
        key=Fernet.generate_key();store=self.store(key);remote=self.remote()
        cp=Checkpoints(remote,store,1)
        self.assertFalse(await cp.recover_and_claim())
        store.touch(2);store.set(2,'session','test-session');store.set(2,'watch',[dict(id='a',src='-100123',target='2',last=44,paused=False)])
        await cp.flush()
        raw=(remote.repo/'state.enc').read_bytes();self.assertNotIn(b'test-session',raw)
        fresh=self.store(key);cp2=Checkpoints(self.remote(),fresh,1)
        self.assertTrue(await cp2.recover_and_claim())
        self.assertEqual(fresh.get(2,'session'),'test-session');self.assertEqual(fresh.get(2,'watch')[0]['last'],44)
    async def test_stale_writer_rejected(self):
        first=self.remote();await first.load();await first.write(b'first')
        second=self.remote();self.assertEqual(await second.load(),b'first');await second.write(b'second')
        with self.assertRaises(StateConflict):await first.write(b'stale')
        self.assertEqual(await second.load(),b'second')
    async def test_dirty_check_avoids_unchanged_pushes(self):
        store=self.store();remote=self.remote();cp=Checkpoints(remote,store,1);await cp.recover_and_claim()
        first=remote.base
        self.assertFalse(await cp.flush());self.assertEqual(remote.base,first)
        store.touch(1)  # same name/username, no write
        self.assertFalse(await cp.flush())
        store.set(1,'caption','changed');self.assertTrue(await cp.flush())
        self.assertNotEqual(remote.base,first)
    async def test_corrupt_remote_does_not_boot_empty_and_overwrite(self):
        remote=self.remote();await remote.load();await remote.write(b'corrupt')
        other=self.remote();store=self.store();cp=Checkpoints(other,store,1)
        with self.assertRaises(ValueError):await cp.recover_and_claim()
        self.assertEqual(await remote.load(),b'corrupt')
    async def test_remote_outage_does_not_overwrite_local_session(self):
        store=self.store();store.set(1,'session','safe')
        remote=NS(load=AsyncMock(side_effect=IOError('offline')),write=AsyncMock())
        with self.assertRaises(IOError):await Checkpoints(remote,store,1).recover_and_claim()
        self.assertEqual(store.get(1,'session'),'safe');remote.write.assert_not_awaited()
    async def test_production_refuses_empty_remote_initialization(self):
        remote=self.remote();store=self.store()
        cp=Checkpoints(remote,store,1,allow_initialize=False)
        with self.assertRaises(IOError):await cp.recover_and_claim()
        self.assertFalse((remote.repo/'state.enc').exists())

    async def test_rolling_branch_has_one_visible_commit(self):
        remote=self.remote();await remote.load()
        await remote.write(b'one');await remote.write(b'two')
        count=(await remote.git('rev-list','--count','HEAD')).strip()
        self.assertEqual(count,b'1')
    async def test_wrong_key_remote_recovery_rejected(self):
        a=self.store();cp=Checkpoints(self.remote(),a,1);await cp.recover_and_claim()
        b=self.store();second=Checkpoints(self.remote(),b,1)
        with self.assertRaises(ValueError):await second.recover_and_claim()

class BotResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AsyncTests.asyncSetUp(self)
        self.before=(bot.RECOVERY,bot.CHECKPOINTS)
        bot.RECOVERY=None;bot.CHECKPOINTS=None
    async def asyncTearDown(self):
        await fixtures.AsyncTests.asyncTearDown(self)
        bot.RECOVERY,bot.CHECKPOINTS=self.before
    async def test_login_is_idempotent_when_connected(self):
        cl=NS(is_connected=lambda:True);bot.USER_CLIENTS[2]=cl
        await bot.command(Event(2),'login','')
        self.assertNotIn(2,bot.LOGINS)
    async def test_private_forward_auto_recovers_saved_session(self):
        bot.DB.set(2,'session','saved')
        cl=NS(is_connected=lambda:True)
        async def connect(uid,session):bot.USER_CLIENTS[uid]=cl;return cl
        bot.RECOVERY=SessionRecovery(bot.DB,bot.USER_CLIENTS,AsyncMock(side_effect=connect))
        e=Event(2);await bot.command(e,'forward','')
        self.assertEqual(bot.PANELS[2]['step'],'first');self.assertNotIn(2,bot.LOGINS)
    async def test_connect_does_not_cancel_its_calling_job(self):
        cl=AsyncMock();cl.is_user_authorized.return_value=True;cl.get_me.return_value=NS(id=2)
        cl.add_event_handler=MagicMock();cl.session=NS(save=lambda:'saved')
        bot.JOBS[2]={'id':'current','task':asyncio.current_task(),'cancel':False}
        with patch('bot.TelegramClient',return_value=cl):await bot.connect_user(2,'')
        self.assertFalse(bot.JOBS[2]['cancel']);bot.JOBS.clear()
    async def test_dialog_warm_failure_keeps_valid_login(self):
        cl=AsyncMock();cl.is_user_authorized.return_value=True;cl.get_me.return_value=NS(id=2)
        cl.get_dialogs.side_effect=ConnectionError('temporary');cl.session=NS(save=lambda:'saved');cl.add_event_handler=MagicMock()
        with patch('bot.TelegramClient',return_value=cl):await bot.connect_user(2,'')
        self.assertIn(2,bot.USER_CLIENTS);self.assertEqual(bot.DB.get(2,'session'),'saved')
    async def test_support_contains_direct_contact_no_secret_request(self):
        e=Event(2);await bot.command(e,'support','')
        self.assertIn('@SyntaxError_2',e.respond.call_args.args[0])
        self.assertEqual(e.respond.call_args.kwargs['buttons'][0][0].type.url,'https://t.me/SyntaxError_2')
    async def test_filtered_album_preserves_all_ids_for_dedupe(self):
        def msg(i,kind):
            return NS(id=i,grouped_id=9,photo=kind=='photo',document=None,sticker=None,voice=None,video_note=None,gif=None,video=kind=='video',audio=None,poll=None,file=None,noforwards=False)
        cfg=bot.cfg_for(2);cfg['type_filters']=['photo']
        with patch('bot.fetcher',AsyncMock(return_value=bot.BOT)),patch('bot.entity',AsyncMock(return_value=NS(noforwards=False))),patch('bot.messages_group',AsyncMock(return_value=[msg(1,'photo'),msg(2,'video')])):
            item=await bot.prepare(2,'abc',1,cfg,1,bot.ROOT/'item',set())
        self.assertEqual(item['ids'],[1,2]);self.assertEqual([m.id for m in item['msgs']],[1])
    async def test_already_delivered_album_members_not_resent(self):
        cfg=bot.cfg_for(2);cfg['_delivered_ids']=[1,2]
        m=NS(id=1,grouped_id=9)
        with patch('bot.fetcher',AsyncMock(return_value=bot.BOT)),patch('bot.entity',AsyncMock(return_value=NS())),patch('bot.messages_group',AsyncMock(return_value=[m,NS(id=2,grouped_id=9)])):
            item=await bot.prepare(2,'abc',2,cfg,2,bot.ROOT/'item',set())
        self.assertTrue(item['skip']);self.assertEqual(item['ids'],[1,2])
    async def test_watch_transient_failure_sets_backoff_without_skipping(self):
        bot.USER_CLIENTS[2]=NS();bot.DB.set(2,'watch',[dict(id='x',src='abc',target='2',last=7,paused=False)])
        with patch('bot.destination_entity',AsyncMock(side_effect=ConnectionError('offline'))):await bot.drain_watch(2,'x')
        route=bot.DB.get(2,'watch')[0]
        self.assertEqual(route['last'],7);self.assertGreater(route['retry_at'],time.time());self.assertEqual(route['failures'],1)
    async def test_watch_does_not_jump_past_interleaved_messages(self):
        bot.USER_CLIENTS[2]=NS();route=dict(id='x',src='abc',target='2',last=0,paused=False)
        bot.DB.set(2,'watch',[route]);messages=[NS(id=i) for i in (1,2,3)]
        cl=NS()
        async def iterator(*a,**kw):
            for m in messages:yield m
        cl.iter_messages=iterator
        prepared=[]
        async def prepare(uid,src,mid,*args):
            prepared.append(mid)
            return {'ids':[1,3] if mid==1 else [2], 'msgs':[], 'skip':False}
        with patch('bot.ensure_session',AsyncMock(return_value=cl)),patch('bot.destination_entity',AsyncMock(return_value=(bot.BOT,NS(),None))),patch('bot.entity',AsyncMock(return_value=NS())),patch('bot.prepare',AsyncMock(side_effect=prepare)),patch('bot.send_prepared',AsyncMock(return_value=1)):
            await bot.drain_watch(2,'x')
        self.assertEqual(prepared,[1,2]);self.assertEqual(bot.DB.get(2,'watch')[0]['last'],3)
    async def test_admin_storage_and_sessions_reject_other_users(self):
        for data in ('adm_storage','adm_sync','adm_sessions_0','adm_ask_retrylogin_1'):
            e=Event(2);e.data=data.encode()
            with self.assertRaises(ValueError):await bot.callbacks(e)
    async def test_successful_sync_failure_does_not_erase_login(self):
        bot.DB.set(2,'session','keep')
        bot.CHECKPOINTS=NS(flush=AsyncMock(side_effect=IOError('offline')))
        with patch('bot.notify',AsyncMock()),self.assertLogs('turbo',level='ERROR'):
            self.assertFalse(await bot.durable_flush(critical=True))
        self.assertEqual(bot.DB.get(2,'session'),'keep')


class MediaIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'video.mp4'
        self.message=NS(file=NS(size=1024*1024),peer_id=NS(),id=7)
    async def asyncTearDown(self):self.tmp.cleanup()
    async def test_failed_preallocation_is_not_accepted_as_complete_media(self):
        from media_io import download_original
        async def broken(message,path):
            with open(path,'wb') as f:f.truncate(message.file.size)
            raise IOError('parallel connection failed')
        client=NS(download_media=AsyncMock(return_value=None))
        with patch('media_io.Parallel',return_value=NS(download=AsyncMock(side_effect=broken))),patch('media_io.asyncio.sleep',AsyncMock()):
            with self.assertRaises(IOError):await download_original(client,self.message,self.path)
        self.assertFalse(self.path.exists());self.assertFalse(self.path.with_name('video.mp4.partial').exists())
    async def test_standard_fallback_creates_atomic_original_file(self):
        from media_io import download_original
        original=b'x'*(1024*1024)
        async def standard(message,file):Path(file).write_bytes(original);return file
        client=NS(download_media=AsyncMock(side_effect=standard))
        with patch('media_io.Parallel',return_value=NS(download=AsyncMock(side_effect=IOError('connection failed')))):
            await download_original(client,self.message,self.path)
        self.assertEqual(self.path.read_bytes(),original)
    async def test_expired_reference_refetched_without_new_login(self):
        from media_io import download_original
        from telethon.errors import FileReferenceExpiredError
        fresh=NS(file=NS(size=3),peer_id=NS(),id=7)
        client=NS(get_messages=AsyncMock(return_value=fresh))
        async def standard(message,file):Path(file).write_bytes(b'HD!');return file
        client.download_media=AsyncMock(side_effect=standard)
        with patch('media_io.Parallel',return_value=NS(download=AsyncMock(side_effect=FileReferenceExpiredError(None)))):
            await download_original(client,self.message,self.path)
        client.get_messages.assert_awaited_once();self.assertEqual(self.path.read_bytes(),b'HD!')
    async def test_cancelled_download_does_not_leave_partial_file(self):
        from media_io import download_original
        async def cancelled(message,path):Path(path).write_bytes(b'partial');raise asyncio.CancelledError()
        with patch('media_io.Parallel',return_value=NS(download=AsyncMock(side_effect=cancelled))):
            with self.assertRaises(asyncio.CancelledError):await download_original(NS(),self.message,self.path)
        self.assertFalse(self.path.exists());self.assertFalse(self.path.with_name('video.mp4.partial').exists())

if __name__=='__main__':unittest.main(verbosity=2)
