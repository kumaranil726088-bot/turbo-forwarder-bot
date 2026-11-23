"""V6.1 UI/admin/backup regression suite (offline, mocked Telegram I/O)."""
import asyncio
import copy
import json
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace as NS
from cryptography.fernet import Fernet
import bot
import control_ui as ui
from help_ui import PAGES, help_page, SLASH_MENU, OWNER_MENU
from storage import Store
import test_v6 as fixtures
from test_v6 import Event

class HelpTests(unittest.TestCase):
    def test_each_page_fits_telegram(self):
        for page in PAGES:
            text,buttons=help_page(page,True)
            self.assertLess(len(text.encode('utf-16-le'))//2,3900,page)
            self.assertNotIn('&lt;',text)
            self.assertIn('\n\n',text)
            for row in buttons:
                for button in row:self.assertLessEqual(len(button.type.data),64)
    def test_owner_page_not_shown_to_users(self):
        text,buttons=help_page('admin',False)
        self.assertEqual(text,help_page('home',False)[0])
        self.assertNotIn(b'help_admin',[b.type.data for row in buttons for b in row])
    def test_menu_covers_every_command_once(self):
        all_commands=SLASH_MENU+OWNER_MENU
        self.assertEqual(len(all_commands),len({k for k,_ in all_commands}))
        self.assertEqual({k for k,_ in all_commands},bot.COMMANDS)
        self.assertLess(len(all_commands),100)
        for cmd,description in all_commands:
            self.assertLessEqual(len(description),256)
            self.assertTrue(cmd.replace('_','').isalnum())

class UIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.AsyncTests.asyncSetUp
    asyncTearDown=fixtures.AsyncTests.asyncTearDown
    async def asyncSetUp(self):
        await fixtures.AsyncTests.asyncSetUp(self)
        ui.INPUT.clear();ui.CONFIRM.clear()
    def module(self):return sys.modules['bot']
    def event(self,data,uid=1):
        e=Event(uid);e.data=data.encode();return e
    async def test_dashboard_has_real_navigation(self):
        e=Event()
        await bot.admin(e,'admin','')
        text=e.respond.call_args.args[0]
        self.assertIn('OWNER CONTROL CENTRE',text)
        buttons=e.respond.call_args.kwargs['buttons']
        actions={b.type.data for row in buttons for b in row}
        self.assertTrue({b'adm_users_0',b'adm_jobs_0',b'adm_routes_0',b'adm_backup'}<=actions)
    async def test_every_admin_entry_rejects_nonowner(self):
        for data in ('adm_home','adm_users_0','adm_user_1','adm_jobs_0','adm_routes_0','adm_backup','adm_access','adm_ask_ban_1','adm_confirm_fake','adm_restore'):
            with self.subTest(data=data),self.assertRaises(ValueError):
                await bot.callbacks(self.event(data,2))
    async def test_user_list_paginates(self):
        for uid in range(3,23):bot.DB.touch(uid,'User '+str(uid),'')
        e=Event();await ui.users(self.module(),e,1)
        text=e.respond.call_args.args[0]
        self.assertIn('Page 2',text)
        self.assertLess(len(text),3500)
        await ui.detail(self.module(),e,2)
        self.assertIn('USER DETAILS',e.respond.call_args.args[0])
    async def test_ban_confirmation_changes_nothing_before_confirm(self):
        e=self.event('adm_ask_ban_2');await bot.callbacks(e)
        self.assertTrue(bot.DB.allowed(2))
        token=next(iter(ui.CONFIRM))
        await bot.callbacks(self.event('adm_confirm_'+token))
        self.assertFalse(bot.DB.allowed(2))
        with self.assertRaises(ValueError):await bot.callbacks(self.event('adm_confirm_'+token))
    async def test_expired_confirmation_rejected(self):
        await ui.ask(self.module(),Event(),'ban',2)
        token=next(iter(ui.CONFIRM));ui.CONFIRM[token]['expires']=0
        with self.assertRaises(ValueError):await ui.apply(self.module(),Event(),token)
        self.assertTrue(bot.DB.allowed(2))
    async def test_legacy_access_button_also_requires_confirmation(self):
        await bot.callbacks(self.event('admin_access'))
        self.assertEqual(bot.DB.global_get('access','open'),'open')
        self.assertEqual(len(ui.CONFIRM),1)

    async def test_navigation_cancels_old_confirmation(self):
        await ui.ask(self.module(),Event(),'ban',2)
        token=next(iter(ui.CONFIRM))
        await bot.callbacks(self.event('help_home'))
        with self.assertRaises(ValueError):await ui.apply(self.module(),Event(),token)
        self.assertTrue(bot.DB.allowed(2))
    async def test_owner_cannot_be_banned(self):
        await ui.ask(self.module(),Event(),'ban',1)
        token=next(iter(ui.CONFIRM))
        with self.assertRaises(ValueError):await ui.apply(self.module(),Event(),token)
    async def test_route_pause_keeps_cursor_and_other_user_routes(self):
        route=dict(id='r1',src='-100100',target='2',last=77,paused=False)
        bot.DB.set(1,'watch',[route]);bot.DB.set(2,'watch',[route])
        await ui.pause_routes(self.module(),2,True)
        self.assertEqual(bot.DB.get(2,'watch')[0]['last'],77)
        self.assertTrue(bot.DB.get(2,'watch')[0]['paused'])
        self.assertFalse(bot.DB.get(1,'watch')[0]['paused'])
        await ui.pause_routes(self.module(),2,True,'r1',delete=True)
        self.assertEqual(bot.DB.get(2,'watch'),[])
        self.assertEqual(len(bot.DB.get(1,'watch')),1)
    async def test_resume_rejects_user_without_login(self):
        bot.DB.set(2,'watch',[dict(id='r1',src='a',target='2',last=7,paused=True)])
        await ui.ask(self.module(),Event(),'resume',2)
        token=next(iter(ui.CONFIRM))
        with self.assertRaises(ValueError):await ui.apply(self.module(),Event(),token)
        self.assertTrue(bot.DB.get(2,'watch')[0]['paused'])
    async def test_whitelist_stops_public_jobs_too(self):
        task=asyncio.create_task(asyncio.sleep(60))
        bot.JOBS[2]={'cancel':False,'task':task}
        await bot.admin(Event(),'access','whitelist')
        self.assertNotIn(2,bot.JOBS);self.assertTrue(task.cancelled())
    async def test_find_user_prompt_and_cancel(self):
        await bot.callbacks(self.event('adm_find'))
        self.assertIn(1,ui.INPUT)
        e=Event(1,'2');await ui.handle_input(self.module(),e)
        self.assertIn('ID: 2',e.respond.call_args.args[0])
        await bot.callbacks(self.event('adm_find'))
        await bot.command(Event(),'cancel','')
        self.assertNotIn(1,ui.INPUT)
    async def test_broadcast_requires_preview_confirmation(self):
        await bot.callbacks(self.event('adm_broadcast'))
        e=Event(1,'Announcement')
        await ui.handle_input(self.module(),e)
        bot.BOT.send_message.assert_not_awaited()
        token=next(iter(ui.CONFIRM))
        await ui.apply(self.module(),Event(),token)
        await asyncio.gather(*list(bot.BACKGROUND))
        self.assertEqual(bot.BOT.send_message.await_count,3)
    async def test_admin_text_does_not_capture_login(self):
        bot.LOGINS[1]={'step':'phone','created':time.time()}
        with self.assertRaises(ValueError):await bot.callbacks(self.event('adm_broadcast'))
        self.assertNotIn(1,ui.INPUT)
    async def test_owner_backup_document_is_encrypted(self):
        bot.DB.set(2,'session','secret-session')
        await bot.admin(Event(),'backup','')
        dest,file=bot.BOT.send_file.call_args.args
        self.assertEqual(dest,1);self.assertTrue(file.name.endswith('.tbackup'))
        blob=file.getvalue();self.assertNotIn(b'secret-session',blob)
        decoded=bot.DB.decode_snapshot(blob,1)
        self.assertEqual(len(decoded['users']),2)
    async def test_backup_roundtrip_and_snapshot_checkpoint(self):
        bot.DB.set(2,'session','user_session');bot.DB.set(2,'caption','saved')
        bot.DB.set(2,'pending_job',{'links':[['abc',1]],'start':0})
        blob=bot.DB.snapshot(1)
        bot.DB.set(2,'caption','newer')
        data=bot.DB.decode_snapshot(blob,1)
        bot.DB.restore_snapshot(data,1)
        self.assertEqual(bot.DB.get(2,'caption'),'saved')
        self.assertEqual(bot.DB.get(2,'session'),'user_session')
        self.assertEqual(bot.DB.get(2,'pending_job')['start'],0)
        self.assertFalse(bot.DB.global_get('broadcast_active'))
    async def test_backup_tampering_wrong_key_owner_rejected(self):
        blob=bot.DB.snapshot(1)
        with self.assertRaises(ValueError):bot.DB.decode_snapshot(blob[:-5]+b'xxxxx',1)
        with self.assertRaises(ValueError):bot.DB.decode_snapshot(blob,2)
        old=bot.DB.cipher;bot.DB.cipher=Fernet(Fernet.generate_key())
        try:
            with self.assertRaises(ValueError):bot.DB.decode_snapshot(blob,1)
        finally:bot.DB.cipher=old
    async def test_invalid_restore_preserves_current_database(self):
        bot.DB.set(2,'caption','keep')
        data=bot.DB.decode_snapshot(bot.DB.snapshot(1),1)
        data['users'].append(list(data['users'][0]))
        with self.assertRaises(ValueError):bot.DB.restore_snapshot(data,1)
        self.assertEqual(bot.DB.get(2,'caption'),'keep')
        self.assertEqual(len(bot.DB.users()),2)
    async def test_restore_requires_reply_and_confirmation(self):
        with self.assertRaises(ValueError):await bot.admin(Event(),'restore','')
        data=bot.DB.decode_snapshot(bot.DB.snapshot(1),1)
        await ui.ask(self.module(),Event(),'restore',payload={'data':data,'summary':'2 users'})
        token=next(iter(ui.CONFIRM));bot.DB.set(2,'caption','later')
        await ui.apply(self.module(),Event(),token)
        self.assertEqual(bot.DB.get(2,'caption'),'')
    async def test_restore_preview_does_not_replace_data(self):
        blob=bot.DB.snapshot(1)
        bot.DB.set(2,'caption','later')
        reply=NS(file=NS(name='copy.tbackup',size=len(blob)),download_media=AsyncMock(return_value=blob))
        e=Event();e.is_reply=True;e.get_reply_message=AsyncMock(return_value=reply)
        await ui.restore_preview(self.module(),e)
        self.assertEqual(bot.DB.get(2,'caption'),'later')
        self.assertEqual(len(ui.CONFIRM),1)
    async def test_reconnect_uses_only_requesting_session(self):
        bot.DB.set(1,'session','owner');bot.DB.set(2,'session','user')
        with patch('bot.connect_user',AsyncMock()) as reconnect:
            await bot.command(Event(2),'reconnect','')
            reconnect.assert_awaited_once_with(2,'user')
    async def test_route_admin_listing_pagination(self):
        bot.DB.set(2,'watch',[dict(id=f'r{i}',src='a',target='2',last=i,paused=False,error='error'*30) for i in range(20)])
        e=Event();await ui.listing(self.module(),e,'routes',2)
        self.assertIn('Page 3',e.respond.call_args.args[0])
        self.assertLess(len(e.respond.call_args.args[0]),3900)
    async def test_personal_watch_pagination(self):
        bot.DB.set(2,'watch',[dict(id=f'r{i}',src='a',target='2',last=i,paused=False,error='error'*30) for i in range(20)])
        e=Event(2);await bot.watch_view(e,1)
        self.assertIn('Page 2',e.respond.call_args.args[0])
        self.assertLess(len(e.respond.call_args.args[0]),3900)
    async def test_long_unicode_responses_split_safely(self):
        e=Event();await bot.respond(e,'🚀'*3000)
        self.assertGreater(e.respond.await_count,1)
        for call in e.respond.call_args_list:self.assertLess(len(call.args[0].encode('utf-16-le'))//2,4096)

if __name__=='__main__':unittest.main(verbosity=2)
