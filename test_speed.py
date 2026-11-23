"""V6.4 transport reuse, isolation, cancellation and path-planning regressions."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock,patch
import bot
import test_v6 as fixtures
from test_v6 import Event
from transfer import Parallel
from transport_pool import ACTIVE_POOL,ConnectionPool
from speed_tools import MetadataCache,ACTIVE_METADATA,transfer_plan

class PoolTests(unittest.IsolatedAsyncioTestCase):
    def transport(self):return NS(open=AsyncMock(),close=AsyncMock(),senders=[NS()])
    async def test_healthy_connection_is_reused(self):
        pool=ConnectionPool();client=object();raw=self.transport()
        e=await pool.acquire(client,1,4,lambda:raw);await pool.release(e)
        reused=await pool.acquire(client,1,4,lambda:self.fail('must reuse'))
        self.assertIs(e,reused);raw.open.assert_awaited_once_with(1)
        self.assertEqual((pool.hits,pool.misses),(1,1))
        await pool.release(reused);await pool.close();raw.close.assert_awaited_once()
    async def test_account_dc_connection_count_isolation(self):
        pool=ConnectionPool(capacity=4);a=object();b=object()
        for key in ((a,1,4),(b,1,4),(a,2,4),(a,1,2)):
            e=await pool.acquire(*key,self.transport);await pool.release(e)
        self.assertEqual(len(pool.entries),4);self.assertEqual(pool.hits,0)
        await pool.close()
    async def test_capacity_waits_and_does_not_share_busy_socket(self):
        pool=ConnectionPool(capacity=1);client=object()
        e=await pool.acquire(client,1,2,self.transport)
        waiting=asyncio.create_task(pool.acquire(client,1,2,self.transport))
        await asyncio.sleep(.01);self.assertFalse(waiting.done());self.assertEqual(len(pool.entries),1)
        await pool.release(e);second=await asyncio.wait_for(waiting,1)
        self.assertIs(e,second);await pool.release(second);await pool.close()
    async def test_idle_lru_evicted_under_global_bound(self):
        pool=ConnectionPool(capacity=1);raw=self.transport()
        e=await pool.acquire(object(),1,2,lambda:raw);await pool.release(e)
        e2=await pool.acquire(object(),1,2,self.transport)
        self.assertEqual(len(pool.entries),1);raw.close.assert_awaited_once()
        await pool.release(e2);await pool.close()
    async def test_failure_and_cancellation_discard(self):
        for exception in (IOError('network'),asyncio.CancelledError()):
            pool=ConnectionPool();raw=self.transport();raw.open.side_effect=exception
            with self.assertRaises(type(exception)):await pool.acquire(object(),1,2,lambda:raw)
            self.assertEqual(pool.entries,[]);raw.close.assert_awaited_once();await pool.close()
    async def test_idle_timeout_reconnects(self):
        pool=ConnectionPool(idle_seconds=0);a=object();first=self.transport();second=self.transport()
        e=await pool.acquire(a,1,2,lambda:first);await pool.release(e)
        e=await pool.acquire(a,1,2,lambda:second)
        first.close.assert_awaited_once();second.open.assert_awaited_once()
        await pool.release(e);await pool.close()
    async def test_logout_retires_only_matching_client(self):
        pool=ConnectionPool();a=object();b=object();ra=self.transport();rb=self.transport()
        ea=await pool.acquire(a,1,2,lambda:ra);eb=await pool.acquire(b,1,2,lambda:rb)
        await pool.release(eb);await pool.close_client(a)
        ra.close.assert_not_awaited();self.assertTrue(ea['retire'])
        await pool.release(ea);ra.close.assert_awaited_once();rb.close.assert_not_awaited()
        await pool.close_client(b);rb.close.assert_awaited_once();self.assertEqual(pool.entries,[])
        await pool.close()
    async def test_close_wakes_waiters_and_rejects_new_lease(self):
        pool=ConnectionPool(capacity=1);a=object();e=await pool.acquire(a,1,2,self.transport)
        waiter=asyncio.create_task(pool.acquire(a,1,2,self.transport));await asyncio.sleep(.01)
        await pool.close()
        with self.assertRaises(RuntimeError):await waiter
        with self.assertRaises(RuntimeError):await pool.acquire(a,1,2,self.transport)
        await pool.release(e)
    async def test_cancelled_waiter_does_not_evict_active_lease(self):
        pool=ConnectionPool(capacity=1);a=object();raw=self.transport()
        e=await pool.acquire(a,1,2,lambda:raw)
        waiter=asyncio.create_task(pool.acquire(a,1,2,self.transport));await asyncio.sleep(.01)
        waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)
        raw.close.assert_not_awaited();self.assertEqual(len(pool.entries),1)
        await pool.release(e);await pool.close()
    async def test_parallel_failure_does_not_return_bad_connection_to_pool(self):
        pool=ConnectionPool();token=ACTIVE_POOL.set(pool)
        raw=self.transport();raw.senders=[NS(send=AsyncMock(side_effect=IOError('broken'))) ]
        raw.open=AsyncMock()
        try:
            client=NS(session=NS(dc_id=1))
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/'x';path.write_bytes(b'x'*1024)
                with patch('transport_pool.ConnectionPool.acquire',AsyncMock(return_value={'transport':raw})):
                    # Register the mocked lease so release exercises invalidation.
                    entry={'transport':raw,'key':(client,1,2),'busy':True,'used':0}
                    pool.entries=[entry]
                    with patch.object(pool,'acquire',AsyncMock(return_value=entry)):
                        with self.assertRaises(IOError):await Parallel(client,2).upload(path)
                raw.close.assert_awaited_once();self.assertEqual(pool.entries,[])
        finally:ACTIVE_POOL.reset(token);await pool.close()

class CrossDcTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        from telethon.crypto import AuthKey
        class Client:
            async def __call__(self,request):
                self.exports+=1
                return NS(id=self.exports,bytes=b'export')
        c=Client();c.exports=0;c.session=NS(dc_id=1,auth_key=AuthKey(b'x'*256))
        c._get_dc=AsyncMock(return_value=NS(id=2,ip_address='dc',port=443))
        c._log={};c._proxy=None;c._local_addr=None;c._init_request=NS(query=None)
        c._connection=lambda *a,**kw:NS();c._borrow_sender_lock=asyncio.Lock()
        return c
    def sender(self):
        from telethon.crypto import AuthKey
        return NS(connect=AsyncMock(),send=AsyncMock(),disconnect=AsyncMock(),auth_key=AuthKey(b'z'*256))
    async def test_one_export_per_remote_pool_followers_share_key_not_auth_bytes(self):
        from telethon.tl.functions.auth import ImportAuthorizationRequest
        from telethon.tl.functions.help import GetConfigRequest
        c=self.client();senders=[self.sender() for _ in range(3)]
        with patch('transfer.MTProtoSender',side_effect=senders) as factory:
            p=Parallel(c,3,reuse=False);await p.open(2)
            self.assertEqual(c.exports,1)
            self.assertIsNone(factory.call_args_list[0].args[0])
            for call in factory.call_args_list[1:]:self.assertEqual(call.args[0].key,b'z'*256)
            self.assertIsInstance(senders[0].send.call_args.args[0].query.query,ImportAuthorizationRequest)
            for sender in senders[1:]:self.assertIsInstance(sender.send.call_args.args[0].query.query,GetConfigRequest)
            await p.close()
    async def test_initial_auth_failure_closes_first_sender_without_starting_followers(self):
        c=self.client();sender=self.sender();sender.send.side_effect=IOError('auth failed')
        with patch('transfer.MTProtoSender',return_value=sender) as factory:
            p=Parallel(c,3,reuse=False)
            with self.assertRaises(IOError):await p.open(2)
            factory.assert_called_once();sender.disconnect.assert_awaited_once()
    async def test_export_import_pair_holds_telethon_borrow_lock(self):
        c=self.client();sender=self.sender()
        async def imported(request):self.assertTrue(c._borrow_sender_lock.locked())
        sender.send.side_effect=imported
        with patch('transfer.MTProtoSender',return_value=sender):
            p=Parallel(c,1,reuse=False);await p.open(2);await p.close()
        self.assertFalse(c._borrow_sender_lock.locked())

class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_lookup_coalesces_and_is_identity_scoped(self):
        cache=MetadataCache();a=object();b=object();loader=AsyncMock(return_value=NS())
        x,y=await asyncio.gather(cache.get((a,'src'),loader),cache.get((a,'src'),loader))
        self.assertIs(x,y);loader.assert_awaited_once()
        await cache.get((b,'src'),loader);self.assertEqual(loader.await_count,2)
    async def test_metadata_expiry_and_bounded_lru(self):
        cache=MetadataCache(limit=2,ttl=0);loader=AsyncMock(return_value=1)
        await cache.get('a',loader);await cache.get('a',loader);self.assertEqual(loader.await_count,2)
        await cache.get('b',loader);await cache.get('c',loader);self.assertEqual(list(cache.values),['b','c'])
    async def test_failed_metadata_is_not_cached(self):
        cache=MetadataCache();loader=AsyncMock(side_effect=[IOError('no access'),1])
        with self.assertRaises(IOError):await cache.get('a',loader)
        self.assertEqual(await cache.get('a',loader),1)

class PlanTests(unittest.TestCase):
    def test_direct_copy_requires_same_session_and_no_protection_or_file_changes(self):
        c=object();source=NS(noforwards=False);m=NS(noforwards=False)
        self.assertTrue(transfer_plan(c,c,source,[m],{'mode':'copy'})[0])
        for sender,src,msg,cfg in [(object(),source,m,{}),(c,NS(noforwards=True),m,{}),(c,source,NS(noforwards=True),{}),(c,source,m,{'rename':'x'}),(c,source,m,{'thumb':True}),(c,source,m,{'mode':'download'})]:
            self.assertFalse(transfer_plan(c,sender,src,[msg],cfg)[0])
    def test_caption_only_does_not_force_download(self):
        c=object()
        self.assertTrue(transfer_plan(c,c,NS(),[NS()],{'mode':'copy','caption':'custom','promo':'hi'})[0])

class BotSpeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AsyncTests.asyncSetUp(self)
        self.old=(bot.RECOVERY,bot.CHECKPOINTS,bot.TRANSPORTS)
        bot.RECOVERY=None;bot.CHECKPOINTS=None;bot.TRANSPORTS=None
    async def asyncTearDown(self):
        await fixtures.AsyncTests.asyncTearDown(self)
        bot.RECOVERY,bot.CHECKPOINTS,bot.TRANSPORTS=self.old
    def message(self):
        return NS(id=1,grouped_id=None,file=NS(size=2*1024**3),media=NS(),message='',entities=[],noforwards=False,
                  sticker=None,voice=None,video_note=None,gif=None,video=True,audio=None,photo=None,poll=None,document=None)
    async def test_allowed_private_large_file_uses_zero_download(self):
        cfg=bot.cfg_for(2);cfg.update(sender='User',mode='copy');client=AsyncMock();bot.USER_CLIENTS[2]=client
        with patch('bot.fetcher',AsyncMock(return_value=client)),patch('bot.entity',AsyncMock(return_value=NS(noforwards=False))),patch('bot.messages_group',AsyncMock(return_value=[self.message()])),patch('bot.download_original',AsyncMock()) as download:
            item=await bot.prepare(2,'-100123',1,cfg,1,bot.ROOT/'direct',set())
            self.assertTrue(item['direct']);self.assertEqual(item['files'],[])
            await bot.send_prepared(2,item,cfg,client,NS(),None)
            download.assert_not_awaited();client.send_file.assert_awaited_once()
    async def test_expired_native_reference_refreshes_without_full_download(self):
        from telethon.errors import FileReferenceExpiredError
        cfg=bot.cfg_for(2);cfg['mode']='copy';client=AsyncMock();m=self.message()
        client.get_messages.return_value=[m]
        client.send_file.side_effect=[FileReferenceExpiredError(None),NS()]
        item=dict(cl=client,source=NS(),msgs=[m],direct=True,files=[],index=1)
        with patch('bot.download_original',AsyncMock()) as download:
            n=await bot.send_prepared(2,item,cfg,client,NS(),None)
        self.assertEqual(n,1);self.assertEqual(client.send_file.await_count,2)
        client.get_messages.assert_awaited_once();download.assert_not_awaited()
    async def test_native_reference_refresh_happens_at_most_once(self):
        from telethon.errors import FileReferenceExpiredError
        item=dict(cl=AsyncMock(),msgs=[self.message()],source=NS(),_native_refreshed=True)
        self.assertIsNone(await bot.refreshed_native_item(item,FileReferenceExpiredError(None)))
        item['cl'].get_messages.assert_not_awaited()
    async def test_missing_refresh_is_content_failure_not_silent_success(self):
        from telethon.errors import FileReferenceExpiredError
        from queue_tools import ItemUnavailable
        client=AsyncMock();client.get_messages.return_value=[]
        with self.assertRaises(ItemUnavailable):
            await bot.refreshed_native_item(dict(cl=client,msgs=[self.message()],source=NS()),FileReferenceExpiredError(None))

    async def test_why_is_read_only_and_explains_sender_mismatch(self):
        with patch('bot.fetcher',AsyncMock(return_value=NS())),patch('bot.entity',AsyncMock(return_value=NS())),patch('bot.messages_group',AsyncMock(return_value=[self.message()])),patch('bot.destination_entity',AsyncMock(return_value=(bot.BOT,NS(),None))),patch('bot.download_original',AsyncMock()) as download:
            event=Event(2);await bot.command(event,'why','https://t.me/c/123/1')
            self.assertIn('sending account differ',event.respond.call_args.args[0])
            download.assert_not_awaited();bot.BOT.send_file.assert_not_awaited();bot.BOT.send_message.assert_not_awaited()
    async def test_why_protected_source_never_claims_native_success(self):
        client=NS()
        with patch('bot.fetcher',AsyncMock(return_value=client)),patch('bot.entity',AsyncMock(return_value=NS(noforwards=True))),patch('bot.messages_group',AsyncMock(return_value=[self.message()])),patch('bot.destination_entity',AsyncMock(return_value=(client,NS(),None))):
            event=Event(2);await bot.command(event,'why','https://t.me/c/123/1')
            self.assertIn('protection is enabled',event.respond.call_args.args[0])
    async def test_job_scope_resets_and_never_serializes_pool_or_metadata(self):
        before_pool=ACTIVE_POOL.get();before_meta=ACTIVE_METADATA.get()
        with patch('bot.destination_entity',AsyncMock(return_value=(bot.BOT,NS(),None))),patch('bot.prepare',AsyncMock(return_value={'skip':True,'ids':[1]})):
            await bot.start_job(Event(2),[('abc',1)])
            while bot.BACKGROUND:await asyncio.gather(*list(bot.BACKGROUND))
        self.assertIs(ACTIVE_POOL.get(),before_pool);self.assertIs(ACTIVE_METADATA.get(),before_meta)
        self.assertIsNone(bot.DB.get(2,'pending_job'))
    async def test_shared_runtime_pool_survives_job_end(self):
        pool=ConnectionPool();bot.TRANSPORTS=pool
        with patch('bot.destination_entity',AsyncMock(side_effect=ValueError('denied'))):
            await bot.start_job(Event(2),[('abc',1)])
            while bot.BACKGROUND:await asyncio.gather(*list(bot.BACKGROUND))
        self.assertFalse(pool.closed);await pool.close()

if __name__=='__main__':unittest.main()
