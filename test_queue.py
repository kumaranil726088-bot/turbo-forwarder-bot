"""V6.3 poll, resume, ordered disk admission and progress regressions."""
import asyncio
import io
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
import unittest
import bot
import test_v6 as fixtures
from test_v6 import Event
from queue_tools import DiskBudget,ResourceLimit,ItemUnavailable,poll_text,nonfatal,text_chunks

class DiskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    async def asyncTearDown(self):self.temp.cleanup()
    async def test_capacity_waits_until_previous_files_release(self):
        budget=DiskBudget(self.root,100,margin=0)
        await budget.reserve(self.root/'one',80)
        waiter=asyncio.create_task(budget.reserve(self.root/'two',40))
        await asyncio.sleep(.01);self.assertFalse(waiter.done())
        await budget.release_tree(self.root/'one');await asyncio.wait_for(waiter,1)
        self.assertEqual(sum(budget.reserved.values()),40)
    async def test_oversized_album_is_explicit_resource_pause(self):
        budget=DiskBudget(self.root,100,margin=0)
        with self.assertRaises(ResourceLimit):await budget.reserve(self.root/'big',101)
    async def test_cancelled_wait_does_not_leak_reservation(self):
        budget=DiskBudget(self.root,100,margin=0);await budget.reserve(self.root/'one',100)
        waiter=asyncio.create_task(budget.reserve(self.root/'two',50));await asyncio.sleep(.01)
        waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)
        self.assertEqual(len(budget.reserved),1)
        await budget.release_tree(self.root);self.assertEqual(budget.reserved,{})
    async def test_sibling_user_reservations_are_isolated_on_cleanup(self):
        budget=DiskBudget(self.root,100,margin=0)
        await budget.reserve(self.root/'user1'/'a',40);await budget.reserve(self.root/'user2'/'a',40)
        await budget.release_tree(self.root/'user1')
        self.assertEqual(list(budget.reserved),[str(self.root/'user2'/'a')])

class PollTests(unittest.TestCase):
    def test_rich_poll_question_answers_rendered_without_hidden_quiz_answers(self):
        m=NS(poll=NS(poll=NS(question=NS(text='2 + 2?'),quiz=True,answers=[NS(text=NS(text='3')),NS(text=NS(text='4'))]),results=NS(total_voters=8)))
        text=poll_text(m)
        self.assertIn('2 + 2?',text);self.assertIn('2. 4',text);self.assertIn('not interactive',text)
        self.assertIn('Source votes at copy time: 8',text)
    def test_global_failures_are_not_silently_skipped(self):
        self.assertTrue(nonfatal(ItemUnavailable('deleted')))
        for exc in (ConnectionError('network'),ResourceLimit('disk'),ValueError('bad config')):self.assertFalse(nonfatal(exc))

class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AsyncTests.asyncSetUp(self)
        self.globals=(bot.DISK,bot.RECOVERY,bot.CHECKPOINTS)
        bot.DISK=None;bot.RECOVERY=None;bot.CHECKPOINTS=None
        self.poll=NS(id=72,poll=NS(poll=NS(question='Pick one',quiz=False,answers=[NS(text='A'),NS(text='B')]),results=None),file=None,message='',entities=[])
    async def asyncTearDown(self):
        await fixtures.AsyncTests.asyncTearDown(self)
        bot.DISK,bot.RECOVERY,bot.CHECKPOINTS=self.globals
    async def wait_job(self):
        while bot.BACKGROUND:await asyncio.gather(*list(bot.BACKGROUND))
    async def test_poll_under_bot_sender_copy_mode_is_text_not_fatal(self):
        item=dict(skip=False,msgs=[self.poll],direct=False,files=[],index=1)
        sender=AsyncMock()
        n=await bot.send_prepared(2,item,bot.cfg_for(2),sender,NS(),42)
        self.assertEqual(n,1);sender.send_message.assert_awaited_once()
        self.assertIn('Pick one',sender.send_message.call_args.args[1])
        self.assertEqual(sender.send_message.call_args.kwargs['reply_to'],42)
    async def test_native_poll_forward_still_used_when_requested_and_allowed(self):
        item=dict(msgs=[self.poll],direct=True,files=[],index=1,source=NS())
        sender=AsyncMock();cfg=bot.cfg_for(2);cfg['mode']='forward'
        await bot.send_prepared(2,item,cfg,sender,NS(),None)
        sender.forward_messages.assert_awaited_once();sender.send_message.assert_not_awaited()
    async def test_video_queue_continues_after_poll_and_records_content_failure(self):
        async def prepare(uid,src,mid,*args,**kwargs):
            if mid==3:raise ItemUnavailable('deleted content')
            return {'skip':False,'msgs':[self.poll] if mid==2 else [NS(id=mid)],'ids':[mid],'index':mid,'direct':False,'files':[]}
        async def send(uid,item,*args):return 1
        with patch('bot.destination_entity',AsyncMock(return_value=(bot.BOT,NS(),None))),patch('bot.prepare',AsyncMock(side_effect=prepare)),patch('bot.send_prepared',AsyncMock(side_effect=send)):
            await bot.start_job(Event(2),[('abc',i) for i in range(1,5)])
            await self.wait_job()
        summary=bot.DB.get(2,'last_job_summary')
        self.assertEqual(summary['done'],4);self.assertEqual(summary['ok'],3);self.assertEqual(summary['failed'],1)
        self.assertEqual(bot.DB.get(2,'failed_items')[0]['mid'],3)
        self.assertIsNone(bot.DB.get(2,'pending_job'))
    async def test_destination_failure_preserves_cursor_instead_of_dropping_all_videos(self):
        with patch('bot.destination_entity',AsyncMock(side_effect=ValueError('no write access'))):
            await bot.start_job(Event(2),[('abc',1),('abc',2)]);await self.wait_job()
        self.assertEqual(bot.DB.get(2,'pending_job')['start'],0)
        self.assertEqual(bot.DB.get(2,'failed_items',[]),[])
    async def test_resume_legacy_checkpoint_uses_current_transport_and_filter(self):
        bot.DB.set(2,'pending_job',{'links':[['abc',1]],'cfg':{'mode':'copy','sender':'Bot','target':'@old','caption':'custom'},'start':0})
        bot.DB.set(2,'mode','forward');bot.DB.set(2,'sender','User');bot.DB.set(2,'type_filters',['video'])
        with patch('bot.start_job',AsyncMock()) as start:await bot.command(Event(2),'resume','')
        cfg=start.call_args.args[2]
        self.assertEqual(cfg['mode'],'forward');self.assertEqual(cfg['sender'],'User');self.assertEqual(cfg['type_filters'],['video'])
        self.assertEqual(cfg['target'],'@old');self.assertEqual(cfg['caption'],'custom')
    async def test_resume_preserves_panel_sender_unless_setting_changed(self):
        cfg=bot.cfg_for(2);cfg.update(sender='User',_settings_baseline={'sender':'Bot','mode':'turbo','workers':2,'connections':4,'delay':.1,'filter':'all','type_filters':bot.DB.get(2,'type_filters')})
        bot.DB.set(2,'pending_job',{'links':[['abc',1]],'cfg':cfg,'start':0})
        with patch('bot.start_job',AsyncMock()) as start:await bot.command(Event(2),'resume','')
        self.assertEqual(start.call_args.args[2]['sender'],'User')
    async def test_resume_latest_explicitly_uses_new_destination(self):
        bot.DB.set(2,'pending_job',{'links':[['abc',1]],'cfg':{'target':'@old'},'start':0})
        bot.DB.set(2,'target','@new')
        with patch('bot.start_job',AsyncMock()) as start:await bot.command(Event(2),'resume','latest')
        self.assertEqual(start.call_args.args[2]['target'],'@new')
    async def test_failure_list_and_retry_are_user_scoped(self):
        cfg=bot.cfg_for(2)
        bot.record_failure(2,'abc',1,cfg,ItemUnavailable('bad'))
        self.assertEqual(bot.DB.get(1,'failed_items',[]),[])
        await bot.command(Event(2),'failed','')
        self.assertEqual(bot.BOT.send_file.call_args.args[0],2)
        with patch('bot.start_job',AsyncMock()) as start:await bot.command(Event(2),'retryfailed','')
        self.assertEqual(start.call_args.args[1],[('abc',1)])
    async def test_retry_does_not_move_failures_to_wrong_destination(self):
        bot.record_failure(2,'abc',1,{'target':'@old'},ItemUnavailable('bad'))
        bot.DB.set(2,'target','@new')
        with self.assertRaises(ValueError):await bot.command(Event(2),'retryfailed','')
    async def test_live_byte_progress_is_per_job_and_monotonic_over_retries(self):
        bot.JOBS[2]={'id':'test'}
        path=str(bot.ROOT/'u_2_test'/'1'/'video.mp4')
        callback=bot.progress_for(2,'download',path)
        callback(100,200);callback(150,200);callback(0,200);callback(200,200)
        self.assertEqual(bot.JOBS[2]['download_bytes'],350)
        self.assertIsNone(bot.progress_for(1,'download',path))
        self.assertIsNone(bot.progress_for(2,'download',str(bot.ROOT/'u_2_watch_route'/'x.mp4')))
        bot.JOBS.clear()
    async def test_admission_preserves_order_even_if_later_metadata_would_be_faster(self):
        first=asyncio.Event();first.set();second=asyncio.Event();third=asyncio.Event()
        calls=[]
        async def core(uid,src,mid,cfg,index,folder,seen,next_admission):
            calls.append(mid)
            return {'direct':True,'ids':[mid]}
        with patch('bot.prepare_core',AsyncMock(side_effect=core)):
            task1=asyncio.create_task(bot.prepare(2,'abc',1,{},1,bot.ROOT/'a',set(),(first,second)))
            task2=asyncio.create_task(bot.prepare(2,'abc',2,{},2,bot.ROOT/'b',set(),(second,third)))
            item=await task1;await asyncio.sleep(.01)
            self.assertEqual(calls,[1]);self.assertFalse(task2.done())
            item['_release_admission'].set();await task2
            self.assertEqual(calls,[1,2])
    async def test_concurrent_start_creates_one_user_job(self):
        blocked=asyncio.Event()
        async def runner(*args,**kwargs):await blocked.wait()
        with patch('bot.run_job',AsyncMock(side_effect=runner)):
            await asyncio.gather(bot.start_job(Event(2),[('abc',1)]),bot.start_job(Event(2),[('abc',2)]))
            self.assertEqual(len(bot.JOBS),1)
            await bot.stop_job(2)
    async def test_retryfailed_respects_topic_and_sender(self):
        cfg=bot.cfg_for(2);cfg['topic']=42
        bot.record_failure(2,'abc',1,cfg,ItemUnavailable('bad'))
        with self.assertRaises(ValueError):await bot.command(Event(2),'retryfailed','')
        bot.DB.set(2,'topic',42)
        with patch('bot.start_job',AsyncMock()) as start:await bot.command(Event(2),'retryfailed','')
        start.assert_awaited_once()
        bot.DB.set(2,'sender','User')
        with self.assertRaises(ValueError):await bot.command(Event(2),'retryfailed','')
    async def test_long_poll_archive_split_without_stopping_on_message_length(self):
        self.poll.poll.poll.question='🚀'*3000
        sender=AsyncMock()
        n=await bot.send_prepared(2,dict(msgs=[self.poll],direct=False,files=[],index=1),bot.cfg_for(2),sender,NS(),None)
        self.assertGreater(n,1)
        for call in sender.send_message.call_args_list:
            self.assertLessEqual(len(call.args[1].encode('utf-16-le'))//2,3500)

    async def test_fast_profile_requires_confirmation_and_does_not_silently_change_sender(self):
        await bot.command(Event(2),'fast','')
        self.assertEqual(bot.DB.get(2,'sender'),'Auto')
        e=Event(2);e.data=b'speed_user'
        with patch('bot.ensure_session',AsyncMock()),patch('bot.destination_entity',AsyncMock(return_value=(NS(),NS(),None))):
            await bot.callbacks(e)
        self.assertEqual(bot.DB.get(2,'sender'),'User');self.assertEqual(bot.DB.get(2,'mode'),'copy')

class ParallelStartupTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return NS(session=NS(dc_id=1,auth_key=NS(key=b'x'*256)),_get_dc=AsyncMock(return_value=NS(id=1,ip_address='1.2.3.4',port=443)),
                  _log={},_connection=lambda *a,**kw:NS(),_proxy=None,_local_addr=None,_init_request=NS(query=None))
    async def test_sender_connections_start_concurrently(self):
        from transfer import Parallel
        ready=asyncio.Event();started=0
        async def connect(*a,**kw):
            nonlocal started
            started+=1
            if started==3:ready.set()
            await ready.wait()
        senders=[NS(connect=AsyncMock(side_effect=connect),send=AsyncMock(),disconnect=AsyncMock()) for _ in range(3)]
        with patch('transfer.MTProtoSender',side_effect=senders):
            pool=Parallel(self.client(),3)
            await asyncio.wait_for(pool.open(1),1)
            self.assertEqual(started,3)
            await pool.close()
        for sender in senders:sender.disconnect.assert_awaited_once()
    async def test_failed_initialization_disconnects_every_started_sender(self):
        from transfer import Parallel
        senders=[NS(connect=AsyncMock(side_effect=IOError('fail') if i==0 else None),send=AsyncMock(),disconnect=AsyncMock()) for i in range(3)]
        with patch('transfer.MTProtoSender',side_effect=senders):
            pool=Parallel(self.client(),3)
            with self.assertRaises(IOError):await pool.open(1)
        for sender in senders:sender.disconnect.assert_awaited_once()

if __name__=='__main__':unittest.main(verbosity=2)
