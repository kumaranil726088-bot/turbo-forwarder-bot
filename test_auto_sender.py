"""V6.5 destination-aware sender selection, preservation and checkpoint tests."""
from datetime import datetime,timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
import unittest
import bot
import test_v6 as fixtures
from test_v6 import Event
from telethon.tl.types import Channel,ChatPhotoEmpty
from speed_tools import transfer_plan

class AutoSenderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AsyncTests.asyncSetUp(self)
        self.old=(bot.RECOVERY,bot.CHECKPOINTS)
        bot.RECOVERY=None;bot.CHECKPOINTS=None
    async def asyncTearDown(self):
        await fixtures.AsyncTests.asyncTearDown(self)
        bot.RECOVERY,bot.CHECKPOINTS=self.old
    def channel(self):
        return Channel(id=123,title='Destination',photo=ChatPhotoEmpty(),date=datetime.now(timezone.utc),broadcast=True)
    async def test_default_auto_private_stays_bot_dm(self):
        cfg=bot.cfg_for(2);self.assertEqual(cfg['sender'],'Auto')
        with patch('bot.entity',AsyncMock(return_value=NS())) as entity,patch('bot.ensure_session',AsyncMock()) as ensure:
            sender,dest,topic=await bot.destination_entity(2,cfg)
        self.assertIs(sender,bot.BOT);ensure.assert_not_awaited()
        self.assertIs(entity.call_args.args[0],bot.BOT)
        self.assertEqual(bot.destination_label(2,cfg),'this bot DM')
    async def test_auto_channel_uses_users_own_verified_session(self):
        own=AsyncMock();own.get_permissions.return_value=NS(is_admin=True)
        channel=self.channel();cfg=bot.cfg_for(2);cfg['target']='@destination'
        with patch('bot.ensure_session',AsyncMock(return_value=own)),patch('bot.entity',AsyncMock(return_value=channel)):
            sender,dest,topic=await bot.destination_entity(2,cfg)
        self.assertIs(sender,own);self.assertIs(dest,channel)
        own.get_permissions.assert_awaited_once();self.assertEqual(cfg['target'],'@destination')
    async def test_auto_channel_permission_denied_does_not_fallback_to_shared_bot(self):
        own=AsyncMock();own.get_permissions.return_value=NS(is_admin=False,is_creator=False)
        cfg=bot.cfg_for(2);cfg['target']='@destination'
        with patch('bot.ensure_session',AsyncMock(return_value=own)),patch('bot.entity',AsyncMock(return_value=self.channel())):
            with self.assertRaises(ValueError):await bot.destination_entity(2,cfg)
        bot.BOT.send_file.assert_not_awaited()
    async def test_auto_external_person_is_not_a_channel_destination(self):
        own=AsyncMock();cfg=bot.cfg_for(2);cfg['target']='@someperson'
        with patch('bot.ensure_session',AsyncMock(return_value=own)),patch('bot.entity',AsyncMock(return_value=NS())):
            with self.assertRaises(ValueError):await bot.destination_entity(2,cfg)
    async def test_explicit_bot_setting_preserved(self):
        bot.DB.set(2,'sender','Bot');own=AsyncMock();own.get_permissions.return_value=NS(is_admin=True)
        cfg=bot.cfg_for(2);cfg['target']='@destination'
        with patch('bot.ensure_session',AsyncMock(return_value=own)),patch('bot.entity',AsyncMock(return_value=self.channel())):
            sender,_,_=await bot.destination_entity(2,cfg)
        self.assertIs(sender,bot.BOT);self.assertEqual(bot.DB.get(2,'sender'),'Bot')
    async def test_explicit_user_private_still_means_saved_messages(self):
        own=AsyncMock();cfg=bot.cfg_for(2);cfg['sender']='User'
        with patch('bot.ensure_session',AsyncMock(return_value=own)):
            sender,dest,_=await bot.destination_entity(2,cfg)
        self.assertIs(sender,own);own.get_me.assert_awaited_once()
        self.assertEqual(bot.destination_label(2,cfg),'Saved Messages')
    async def test_prepare_auto_channel_and_destination_use_same_client_without_download(self):
        from test_speed import BotSpeedTests
        own=AsyncMock();bot.USER_CLIENTS[2]=own
        cfg=bot.cfg_for(2);cfg['target']='@destination'
        with patch('bot.fetcher',AsyncMock(return_value=own)),patch('bot.entity',AsyncMock(return_value=self.channel())),patch('bot.messages_group',AsyncMock(return_value=[BotSpeedTests.message(self)])),patch('bot.download_original',AsyncMock()) as download:
            item=await bot.prepare(2,'private_source',1,cfg,1,bot.ROOT/'direct',set())
        self.assertTrue(item['direct']);download.assert_not_awaited()
    async def test_setchannel_updates_paused_destination_not_cursor_links_or_caption(self):
        bot.DB.set(2,'pending_job',{'links':[['abc',1],['abc',2]],'start':1,'cfg':{'target':'2','sender':'Bot','caption':'keep','mode':'copy'}})
        with patch('bot.destination_entity',AsyncMock(return_value=(NS(),NS(),None))):
            await bot.command(Event(2),'setchannel','@destination')
        pending=bot.DB.get(2,'pending_job')
        self.assertEqual(pending['start'],1);self.assertEqual(pending['links'],[['abc',1],['abc',2]])
        self.assertEqual(pending['cfg']['target'],'@destination');self.assertEqual(pending['cfg']['sender'],'Auto')
        self.assertEqual(pending['cfg']['caption'],'keep')
        self.assertEqual(bot.DB.get(2,'target'),'@destination');self.assertIsNone(bot.DB.get(1,'target'))
    async def test_invalid_destination_does_not_change_any_setting_or_checkpoint(self):
        pending={'links':[['abc',1]],'start':0,'cfg':{'target':'2'}};bot.DB.set(2,'pending_job',pending)
        with patch('bot.destination_entity',AsyncMock(side_effect=ValueError('no rights'))):
            with self.assertRaises(ValueError):await bot.command(Event(2),'setchannel','@notallowed')
        self.assertEqual(bot.DB.get(2,'pending_job'),pending);self.assertIsNone(bot.DB.get(2,'target'))
    async def test_active_destination_change_blocked_without_mutation(self):
        bot.JOBS[2]={'id':'active'}
        with self.assertRaises(ValueError):await bot.command(Event(2),'setchannel','@destination')
        self.assertIsNone(bot.DB.get(2,'target'));bot.JOBS.clear()
    async def test_sets_sender_auto_changes_no_destination_and_updates_paused_sender(self):
        bot.DB.set(2,'sender','Bot');bot.DB.set(2,'target','@destination')
        bot.DB.set(2,'pending_job',{'links':[['abc',1]],'start':0,'cfg':{'target':'@destination','sender':'Bot'}})
        with patch('bot.destination_entity',AsyncMock(return_value=(NS(),NS(),None))):
            await bot.command(Event(2),'setsender','auto')
        self.assertEqual(bot.DB.get(2,'sender'),'Auto');self.assertEqual(bot.DB.get(2,'target'),'@destination')
        self.assertEqual(bot.DB.get(2,'pending_job')['cfg']['sender'],'Auto')
    async def test_fast_auto_button_preserves_destination(self):
        bot.DB.set(2,'target','@destination');e=Event(2);e.data=b'speed_auto'
        with patch('bot.destination_entity',AsyncMock(return_value=(NS(),NS(),None))):await bot.callbacks(e)
        self.assertEqual(bot.DB.get(2,'sender'),'Auto');self.assertEqual(bot.DB.get(2,'target'),'@destination')
    async def test_failure_matching_uses_effective_sender_not_policy_label(self):
        from queue_tools import ItemUnavailable
        cfg=bot.cfg_for(2);cfg['target']='@destination'
        bot.record_failure(2,'abc',1,cfg,ItemUnavailable('bad'))
        self.assertEqual(bot.DB.get(2,'failed_items')[0]['sender'],'User')
        bot.DB.set(2,'target','@destination');bot.DB.set(2,'sender','User')
        with patch('bot.start_job',AsyncMock()) as start:await bot.command(Event(2),'retryfailed','')
        start.assert_awaited_once()
    async def test_protection_reason_has_priority_over_account_mismatch(self):
        direct,reason=transfer_plan(object(),object(),NS(noforwards=True),[NS()],{})
        self.assertFalse(direct);self.assertIn('protection',reason)
    async def test_auto_selected_destination_keeps_explicit_topic(self):
        cfg=bot.cfg_for(2);cfg['target']='-100123:42'
        self.assertEqual(bot.configured_sender(2,cfg),'User')
        self.assertEqual(bot.destination_label(2,cfg),'-100123:42')
    async def test_empty_destination_is_not_silently_saved_as_private_default(self):
        with self.assertRaises(ValueError):await bot.command(Event(2),'setchannel','')
        self.assertIsNone(bot.DB.get(2,'target'))
    async def test_change_destination_drops_old_destination_forum_map(self):
        bot.DB.set(2,'pending_job',{'links':[['abc',1]],'start':0,'cfg':{'target':'@old'},'topic_map':{'9':77}})
        with patch('bot.destination_entity',AsyncMock(return_value=(NS(),NS(),None))):
            await bot.command(Event(2),'setchannel','@new:42')
        pending=bot.DB.get(2,'pending_job')
        self.assertIsNone(pending['topic_map']);self.assertEqual(pending['cfg']['target'],'@new:42')
    async def test_batch_started_during_validation_cannot_have_its_checkpoint_retargeted(self):
        async def validated(*args):bot.JOBS[2]={'id':'started'};return (NS(),NS(),None)
        with patch('bot.destination_entity',AsyncMock(side_effect=validated)):
            with self.assertRaises(ValueError):await bot.command(Event(2),'setchannel','@new')
        self.assertIsNone(bot.DB.get(2,'target'));bot.JOBS.clear()

    async def test_explicit_global_auto_choice_overrides_old_panel_sender_on_resume(self):
        bot.DB.set(2,'sender','Auto')
        bot.DB.set(2,'pending_job',{'links':[['abc',1]],'start':0,'cfg':{'target':'@dest','sender':'User','_settings_baseline':{'sender':'Bot'}}})
        with patch('bot.start_job',AsyncMock()) as start:await bot.command(Event(2),'resume','')
        self.assertEqual(start.call_args.args[2]['sender'],'Auto')
    async def test_old_default_bot_inherits_auto_but_destination_does_not_change(self):
        bot.DB.set(2,'pending_job',{'links':[['abc',1]],'start':0,'cfg':{'target':'@dest','sender':'Bot','_settings_baseline':{'sender':'Bot'}}})
        with patch('bot.start_job',AsyncMock()) as start:await bot.command(Event(2),'resume','')
        self.assertEqual(start.call_args.args[2]['sender'],'Auto')
        self.assertEqual(start.call_args.args[2]['target'],'@dest')

    async def test_cycle_includes_all_three_sender_choices(self):
        self.assertEqual(bot.cycle_sender('Auto'),'User');self.assertEqual(bot.cycle_sender('User'),'Bot')
        self.assertEqual(bot.cycle_sender('Bot'),'Auto')

if __name__=='__main__':unittest.main()
