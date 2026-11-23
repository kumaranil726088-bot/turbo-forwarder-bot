"""Offline unit + mocked integration tests; not a substitute for live account tests."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.fernet import Fernet
from telethon.tl.types import DocumentAttributeFilename
import bot
from storage import Store
from transfer import parse_links, parse_post_link, source_ref, destination, renamed, topic_id, Parallel, CHUNK

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name)/'test.db')
        self.key = Fernet.generate_key()
        self.db = Store(self.path,1,self.key)
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def test_isolation(self):
        self.db.set(1,'target','@one'); self.db.set(2,'target','@two')
        self.db.set(1,'watch',[{'src':'a'}])
        self.assertEqual(self.db.get(2,'target'),'@two')
        self.assertEqual(self.db.get(2,'watch'),[])
        copy = self.db.get(2,'watch'); copy.append(1)
        self.assertEqual(self.db.get(2,'watch'),[])
    def test_encrypt(self):
        self.db.set(1,'session','very_secret_session')
        self.assertEqual(self.db.get(1,'session'),'very_secret_session')
        raw = self.db.db.execute("SELECT value FROM user_settings WHERE key='session'").fetchone()[0]
        self.assertNotIn('very_secret_session',raw)
        self.assertIsNone(self.db.get(2,'session'))
    def test_reset_isolation(self):
        self.db.set(1,'session','secret');self.db.set(1,'caption','one');self.db.set(2,'caption','two')
        self.db.reset(1,keep=True)
        self.assertEqual(self.db.get(1,'session'),'secret')
        self.assertEqual(self.db.get(1,'caption'),'')
        self.assertEqual(self.db.get(2,'caption'),'two')
        self.db.reset(1,False);self.assertIsNone(self.db.get(1,'session'))
    def test_access(self):
        self.db.touch(2);self.assertTrue(self.db.allowed(2))
        self.db.flag(2,'banned',1);self.assertFalse(self.db.allowed(2))
        self.db.flag(2,'banned',0);self.db.global_set('access','whitelist')
        self.assertFalse(self.db.allowed(2));self.assertTrue(self.db.allowed(1))
        self.db.flag(2,'role','approved');self.assertTrue(self.db.allowed(2))
    def test_migration_idempotent(self):
        self.db.close()
        os.unlink(self.path)
        c = sqlite3.connect(self.path)
        c.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        c.executemany('INSERT INTO settings VALUES(?,?)',[('user_session','legacy_secret'),('target','-100123'),('watch','[{"src":"@old","target":"@dest"}]'),('workers','3')])
        c.commit();c.close()
        self.db = Store(self.path,1,self.key)
        self.assertEqual(self.db.get(1,'session'),'legacy_secret')
        self.assertEqual(self.db.get(1,'workers'),3)
        self.assertEqual(self.db.get(2,'workers'),2)
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM settings').fetchone()[0],0)
        self.db.set(1,'workers',4);self.db.migrate();self.assertEqual(self.db.get(1,'workers'),4)
    def test_reopen(self):
        self.db.set(2,'pending_job',{'start':8});self.db.close()
        self.db = Store(self.path,1,self.key)
        self.assertEqual(self.db.get(2,'pending_job'),{'start':8})

class ParserTests(unittest.TestCase):
    def test_links(self):
        self.assertEqual(parse_post_link('t.me/c/1234567890/22/55'),('-1001234567890',55))
        self.assertEqual(parse_links('t.me/hello/1-3 t.me/c/123/6/9 t.me/other/7'),[('hello',1),('hello',2),('hello',3),('-100123',9),('other',7)])
    def test_bounds(self):
        self.assertEqual(len(parse_links('t.me/hello/1-10000')),10000)
        with self.assertRaises(ValueError):parse_links('t.me/hello/1-10001')
        with self.assertRaises(ValueError):parse_links('t.me/hello/0')
    def test_dest(self):
        self.assertEqual(source_ref('https://t.me/c/123456/55'),'-100123456')
        self.assertEqual(destination('-100123:42'),('-100123',42))
        self.assertEqual(destination('https://t.me/c/123/42/77'),('-100123',42))
    def test_rename(self):
        self.assertEqual(renamed('old.mp4','old|new',1),'new.mp4')
        self.assertNotIn('/',renamed('original.mp4','../../evil',1))
    def test_topic(self):
        self.assertEqual(topic_id(NS(reply_to=NS(forum_topic=True,reply_to_top_id=22,reply_to_msg_id=80))),22)
        self.assertEqual(topic_id(NS(reply_to=NS(forum_topic=True,reply_to_top_id=None,reply_to_msg_id=22))),22)

class Event:
    def __init__(self,uid=1,text=''):
        self.sender_id=uid;self.raw_text=text;self.is_private=True;self.is_reply=False
        self.photo=None;self.document=None;self.file=None;self.chat_id=uid
        self.message=NS(id=1,reply_to=None,message=text)
        self.respond=AsyncMock(return_value=NS(edit=AsyncMock()))
        self.delete=AsyncMock();self.get_sender=AsyncMock(return_value=NS(first_name='Test',username='test'))
    async def edit(self,*a,**kw):return await self.respond(*a,**kw)

class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_root=bot.ROOT;bot.ROOT=Path(self.tmp.name)/'downloads';bot.ROOT.mkdir()
        bot.DB=Store(str(Path(self.tmp.name)/'db'),1,Fernet.generate_key())
        bot.OWNER_ID=1;bot.USERNAME='TestBot';bot.BOT=AsyncMock()
        bot.TRANSFER_LIMIT=asyncio.Semaphore(2)
        bot.USER_CLIENTS.clear();bot.LOGINS.clear();bot.PANELS.clear();bot.JOBS.clear();bot.WATCH_TASKS.clear();bot.BACKGROUND.clear()
        bot.DB.touch(1);bot.DB.touch(2)
    async def asyncTearDown(self):
        for t in list(bot.BACKGROUND):t.cancel()
        await asyncio.gather(*list(bot.BACKGROUND),return_exceptions=True)
        bot.DB.close();bot.ROOT=self.old_root;self.tmp.cleanup()
    async def test_all_simple_commands(self):
        cases={'start':'','help':'','id':'','setmode':'download','setfilter':'text','settypes':'photo,video','setworkers':'3','setconnections':'4','setdelay':'0.1','setpoll':'3','setcaption':'{original}','clearcaption':'','replace':'old|new','replaces':'','clearreplace':'','settings':'','reset':'','status':'','stats':'','myplan':'','dyno_status':'','ping':'','cleanup_ram':'','cancellogin':'','cancel':'','stop':'','discard':'','watchlist':'','unwatch':'','publicforward':'','admin':'','users':'','alljobs':'','access':'open','ban':'2','unban':'2','approve':'2'}
        for cmd,arg in cases.items():
            with self.subTest(cmd=cmd):
                await bot.command(Event(),cmd,arg)
    async def test_suffix_and_group_gate(self):
        e=Event(2,'/settings@TestBot');await bot.dispatch(e)
        self.assertTrue(e.respond.called)
        e=Event(2,'/settings@OtherBot');await bot.dispatch(e);e.respond.assert_not_called()
        e=Event(2,'/login@TestBot');e.is_private=False
        await bot.dispatch(e);self.assertNotIn(2,bot.LOGINS)
    async def test_user_settings_and_admin_gate(self):
        await bot.command(Event(1),'setcaption','owner')
        await bot.command(Event(2),'setcaption','other')
        self.assertEqual(bot.DB.get(1,'caption'),'owner');self.assertEqual(bot.DB.get(2,'caption'),'other')
        for cmd in bot.ADMIN_COMMANDS:
            with self.assertRaises(ValueError):await bot.admin(Event(2),cmd,'')
    async def test_panel_rename_does_not_pollute_rules(self):
        st=dict(step='set_rename',cfg=bot.cfg_for(2),src='abc',first=1,last=3)
        bot.PANELS[2]=st
        await bot.panel_input(Event(2,'old|new'))
        self.assertEqual(st['cfg']['rename'],'old|new');self.assertEqual(bot.DB.get(2,'replacements'),[])
        m=NS(message='old caption',entities=[],file=NS(name='old.mp4'))
        self.assertEqual(bot.caption(m,st['cfg'],1)[0],'new caption')
    async def test_entity_cache_warm(self):
        cl=AsyncMock();cl.get_entity.side_effect=[ValueError('cache'),NS(id=1)]
        await bot.entity(cl,'-100123');cl.get_dialogs.assert_awaited_once_with(limit=None)
    async def test_private_source_requires_login(self):
        with self.assertRaises(ValueError):await bot.fetcher(2,'-100123')
        with self.assertRaises(ValueError):await bot.destination_entity(2,{'target':'@channel'})
    async def test_session_identity(self):
        cl=AsyncMock();cl.is_user_authorized.return_value=True;cl.get_me.return_value=NS(id=999)
        with patch('bot.TelegramClient',return_value=cl):
            with self.assertRaises(ValueError):await bot.connect_user(2,'')
        self.assertNotIn(2,bot.USER_CLIENTS);cl.disconnect.assert_awaited_once()
    async def test_watch_hook_registered(self):
        cl=AsyncMock();cl.is_user_authorized.return_value=True;cl.get_me.return_value=NS(id=2)
        cl.add_event_handler=MagicMock();cl.session=NS(save=lambda:'session')
        with patch('bot.TelegramClient',return_value=cl):await bot.connect_user(2,'')
        self.assertEqual(cl.add_event_handler.call_count,2)
        self.assertEqual(bot.DB.get(2,'session'),'session')
    async def test_parallel_download_chunks_and_cleanup(self):
        size=CHUNK*3+123
        source=b'a'*size
        p=Parallel(NS(),3)
        senders=[]
        for i in range(3):
            s=NS(send=AsyncMock(side_effect=lambda req:NS(bytes=source[req.offset:req.offset+req.limit])),disconnect=AsyncMock())
            senders.append(s)
        async def open_pool(dc):p.senders=senders[:]
        p.open=open_pool
        path=str(Path(self.tmp.name)/'file.bin')
        m=NS(media=NS(),file=NS(size=size))
        with patch('transfer.utils._get_file_info',return_value=NS(dc_id=2,location=NS())):
            await p.download(m,path)
        self.assertEqual(Path(path).read_bytes(),source)
        for s in senders:s.disconnect.assert_awaited_once()
    async def test_parallel_upload_small_md5_and_big(self):
        for size in (CHUNK+17,11*1024*1024):
            p=Parallel(NS(session=NS(dc_id=1)),3)
            senders=[NS(send=AsyncMock(return_value=True),disconnect=AsyncMock()) for _ in range(3)]
            async def opened(dc):p.senders=senders[:]
            p.open=opened
            path=Path(self.tmp.name)/'x.bin';path.write_bytes(b'z'*size)
            uploaded=await p.upload(str(path))
            self.assertEqual(uploaded.parts,(size+CHUNK-1)//CHUNK)
            self.assertEqual(sum(s.send.await_count for s in senders),uploaded.parts)
            if size<10*1024*1024:
                import hashlib
                self.assertEqual(uploaded.md5_checksum,hashlib.md5(path.read_bytes()).hexdigest())
            for s in senders:s.disconnect.assert_awaited_once()
    async def test_parallel_failure_cancels_workers(self):
        p=Parallel(NS(),2)
        bad=NS(send=AsyncMock(side_effect=IOError('fail')),disconnect=AsyncMock())
        slow=NS(send=AsyncMock(side_effect=lambda r:NS(bytes=b'x'*CHUNK)),disconnect=AsyncMock())
        async def opened(dc):p.senders=[bad,slow]
        p.open=opened
        with patch('transfer.utils._get_file_info',return_value=NS(dc_id=1,location=NS())):
            with self.assertRaises(IOError):await p.download(NS(media=None,file=NS(size=CHUNK*2)),str(Path(self.tmp.name)/'bad'))
        bad.disconnect.assert_awaited_once();slow.disconnect.assert_awaited_once()
    async def test_topic_api_pagination(self):
        cl=AsyncMock();cl.return_value=NS(topics=[NS(id=1),NS(id=2)],messages=[])
        got=await bot.get_topics(cl,NS())
        self.assertEqual(len(got),2)
        self.assertEqual(type(cl.call_args.args[0]).__name__,'GetForumTopicsRequest')
    async def test_no_crosssession_native_forward(self):
        cl=AsyncMock();source=NS(id=9,noforwards=False)
        m=NS(id=1,grouped_id=None,file=None,media=None,message='hi',entities=[],action=None,sticker=None,voice=None,video_note=None,gif=None,video=None,audio=None,photo=None,poll=None,document=None,noforwards=False)
        bot.USER_CLIENTS[2]=cl
        with patch('bot.entity',AsyncMock(return_value=source)),patch('bot.messages_group',AsyncMock(return_value=[m])):
            item=await bot.prepare(2,'abc',1,bot.cfg_for(2),1,Path(self.tmp.name)/'a',set())
        self.assertFalse(item['direct'])
    async def test_job_pipeline_and_checkpoint_isolation(self):
        e=Event(2)
        with patch('bot.destination_entity',AsyncMock(return_value=(bot.BOT,NS(),None))),patch('bot.prepare',AsyncMock(return_value={'skip':True,'ids':[1]})):
            await bot.start_job(e,[('abc',1),('abc',2)])
            await asyncio.gather(*list(bot.BACKGROUND))
        self.assertNotIn(2,bot.JOBS);self.assertIsNone(bot.DB.get(2,'pending_job'))
        self.assertIsNone(bot.DB.get(1,'pending_job'))
    async def test_watch_error_does_not_advance_cursor(self):
        bot.USER_CLIENTS[2]=AsyncMock()
        route=dict(id='test',src='-100123',target='2',last=7,paused=False)
        bot.DB.set(2,'watch',[route])
        with patch('bot.destination_entity',AsyncMock(side_effect=ValueError('no access'))):await bot.drain_watch(2,'test')
        saved=bot.DB.get(2,'watch')[0]
        self.assertEqual(saved['last'],7);self.assertIn('no access',saved['error'])
    async def test_route_callback_isolation(self):
        bot.DB.set(1,'watch',[dict(id='owner',src='a',target='1')])
        e=Event(2);e.data=b'watch_del_owner'
        with self.assertRaises(ValueError):await bot.callbacks(e)
        self.assertEqual(len(bot.DB.get(1,'watch')),1)
    async def test_callbacks_real_mutation(self):
        for data in (b'set_auto',b'set_mode',b'pn_cancel',b'reset_cancel',b'save_private'):
            e=Event(2);e.data=data
            if data==b'pn_cancel':bot.PANELS[2]=dict(step='panel',cfg=bot.cfg_for(2))
            await bot.callbacks(e)
        self.assertFalse(bot.DB.get(2,'auto'))
        self.assertEqual(bot.DB.get(2,'mode'),'forward')
        self.assertEqual(bot.DB.get(2,'target'),'2')

class MoreIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = AsyncTests.asyncSetUp
    asyncTearDown = AsyncTests.asyncTearDown
    # Reuse fixture only, not inherited tests (unittest also runs inherited tests).
    async def test_batch_topic_and_resume(self):
        e=Event(2)
        with patch('bot.start_job',AsyncMock()) as start:
            await bot.command(e,'batch','t.me/c/123456/22/50 55')
            self.assertEqual(start.call_args.args[1],[('-100123456',i) for i in range(50,56)])
            bot.DB.set(2,'pending_job',{'links':[['abc',1]],'start':0})
            await bot.command(e,'resume','')
            self.assertEqual(start.await_count,2)
    async def test_real_forum_creation_not_title_message(self):
        cl=AsyncMock();sender=AsyncMock()
        src=NS(id=10,forum=True);dst=NS(id=20,forum=True)
        cl.get_messages.return_value=[NS(id=5,action=None)]
        sender.return_value=NS(updates=[NS(message=NS(id=88,action=NS()))])
        # Use an actual Telegram action class to exercise created-topic matching.
        from telethon.tl.types import MessageActionTopicCreate
        sender.return_value.updates[0].message.action=MessageActionTopicCreate('Topic A',0)
        with patch('bot.fetcher',AsyncMock(return_value=cl)),patch('bot.entity',AsyncMock(return_value=src)),patch('bot.destination_entity',AsyncMock(return_value=(sender,dst,None))),patch('bot.get_topics',AsyncMock(side_effect=[[NS(id=1,title='General'),NS(id=7,title='Topic A')],[NS(id=1,title='General')]])),patch('bot.utils.get_peer_id',side_effect=lambda e:e.id),patch('bot.start_job',AsyncMock()) as start:
            await bot.group_transfer(Event(2),'@source 100')
        req=sender.call_args.args[0]
        self.assertEqual(type(req).__name__,'CreateForumTopicRequest')
        self.assertEqual(start.call_args.kwargs['topic_map']['7'],88)
        sender.send_message.assert_not_awaited()
    async def test_single_and_album_send_shapes(self):
        sender=AsyncMock()
        await bot.send_media(sender,NS(),['one'],['cap'],[[]],42)
        self.assertEqual(sender.send_file.call_args.args[1],'one')
        self.assertEqual(sender.send_file.call_args.kwargs['reply_to'],42)
        await bot.send_media(sender,NS(),['one','two'],['a','b'],[[],[]],42)
        self.assertEqual(sender.send_file.call_args.args[1],['one','two'])
    async def test_unwatch_only_requesting_user(self):
        r=dict(id='abc',src='-100123',target='2')
        bot.DB.set(1,'watch',[r]);bot.DB.set(2,'watch',[r])
        await bot.command(Event(2),'unwatch','all')
        self.assertEqual(bot.DB.get(2,'watch'),[]);self.assertEqual(bot.DB.get(1,'watch'),[r])
    async def test_login_keypad_and_cancel(self):
        e=Event(2)
        await bot.command(e,'login','')
        self.assertEqual(bot.LOGINS[2]['step'],'phone')
        bot.LOGINS[2].update(step='code',code='')
        e.data=b'lg_digit_5';await bot.callbacks(e)
        self.assertEqual(bot.LOGINS[2]['code'],'5')
        e.data=b'lg_back';await bot.callbacks(e)
        self.assertEqual(bot.LOGINS[2]['code'],'')
        e.data=b'lg_cancel';await bot.callbacks(e)
        self.assertNotIn(2,bot.LOGINS)
    async def test_export_requires_own_session(self):
        bot.DB.set(1,'session','owner-secret')
        with self.assertRaises(ValueError):await bot.command(Event(2),'exportsession','')
        bot.DB.set(2,'session','user-secret')
        await bot.command(Event(2),'exportsession','')
        e=Event(2);e.data=b'export_confirm';await bot.callbacks(e)
        self.assertEqual(bot.BOT.send_file.call_args.args[0],2)
    async def test_reset_keep_does_not_touch_other_user(self):
        bot.DB.set(1,'caption','owner');bot.DB.set(2,'caption','user');bot.DB.set(2,'session','secret')
        e=Event(2);e.data=b'reset_keep';await bot.callbacks(e)
        self.assertEqual(bot.DB.get(2,'caption'),'');self.assertEqual(bot.DB.get(2,'session'),'secret')
        self.assertEqual(bot.DB.get(1,'caption'),'owner')
    async def test_broadcast_delivery_counts(self):
        await bot.admin(Event(1),'broadcast','hello')
        await asyncio.gather(*list(bot.BACKGROUND))
        self.assertFalse(bot.DB.global_get('broadcast_active'))
        self.assertEqual(bot.BOT.send_message.await_count,3)
    async def test_stop_before_job_task_starts(self):
        with patch('bot.destination_entity',AsyncMock(return_value=(bot.BOT,NS(),None))):
            await bot.start_job(Event(2),[('abc',1)])
            await bot.stop_job(2)
        self.assertNotIn(2,bot.JOBS)
        self.assertIsNotNone(bot.DB.get(2,'pending_job'))

if __name__=='__main__':unittest.main(verbosity=2)
