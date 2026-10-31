"""Synthetic HA API only: never contact or switch real devices."""
import asyncio
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestServer
from app.home_assistant import HomeAssistant, connection
from app.ha_action_client import HomeAssistantClient
from app.domain import Invalid
from app.plugins import process, RUNNER
import sys

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+afoUAAAAASUVORK5CYII=')

class HomeAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.color_modes=[];self.brightness=None;self.service_data=[];self.calls=[];self.light='off';self.fail_camera=False;self.fail_restore=False
        async def api(request):
            self.assertEqual(request.headers.get('Authorization'),'Bearer private-test-token')
            path=request.match_info['path'];self.calls.append((request.method,path))
            if path=='states':return web.json_response([
                {'entity_id':'camera.meter','state':'idle','attributes':{'friendly_name':'Meter camera','access_token':'never-return'}},
                {'entity_id':'light.meter','state':self.light,'attributes':{'friendly_name':'Meter light','secret':'never-return'}},
                {'entity_id':'camera.offline','state':'unavailable','attributes':{}},
                {'entity_id':'sensor.secret','state':'sensitive','attributes':{}}])
            if path.startswith('states/'):
                return web.json_response({'state':self.light if path.endswith('light.meter') else 'idle','attributes':{'access_token':'never-return','brightness':self.brightness,'supported_color_modes':self.color_modes}})
            if path.startswith('camera_proxy/'):
                if self.fail_camera:return web.Response(status=500,text='private-test-token')
                return web.Response(body=PNG,content_type='image/png')
            if path.startswith('services/'):
                if path.endswith('/turn_off') and self.fail_restore:return web.Response(status=500)
                data=await request.json();self.service_data.append(data)
                if self.fail_restore and data.get('brightness') not in (None,255):return web.Response(status=500)
                if 'brightness' in data:self.brightness=data['brightness']
                self.light='on' if path.endswith('/turn_on') else 'off'
                return web.json_response([])
            return web.Response(status=404)
        app=web.Application();app.router.add_route('*','/api/{path:.*}',api)
        self.server=TestServer(app);await self.server.start_server()
        self.env=patch.dict(os.environ,{'HA_AI_STUDIO_DEV':'1','HA_URL':str(self.server.make_url('')).rstrip('/'),'HA_ACCESS_TOKEN':'private-test-token'})
        self.env.start();self.gateway=HomeAssistant(self.root)
        self.settings={'capture':{'camera_entity':'camera.meter','light_entity':'light.meter'}}
    async def asyncTearDown(self):
        await self.gateway.close();self.env.stop();await self.server.close();self.tmp.cleanup()
    async def call(self,client,method,*args):return await asyncio.to_thread(getattr(client,method),*args)

    async def test_filtered_search_excludes_attributes_secrets_and_other_domains(self):
        result=await self.gateway.search(['camera'],'meter')
        self.assertEqual([r['entity_id'] for r in result['entities']],['camera.meter'])
        self.assertNotIn('never-return',json.dumps(result));self.assertNotIn('private-test-token',json.dumps(result))
        offline=await self.gateway.search(['camera'],'offline');self.assertFalse(offline['entities'][0]['available'])
        with self.assertRaises(Invalid):await self.gateway.search(['lock'],'')
    async def test_capture_cleanup_scope_limit_and_no_token_in_client(self):
        async with self.gateway.session(self.settings) as access:
            self.assertNotIn('private-test-token',json.dumps(access));client=HomeAssistantClient(access)
            self.assertEqual(await self.call(client,'capture','camera.meter'),PNG)
            await self.call(client,'capture','camera.meter')
            with self.assertRaisesRegex(ValueError,'two camera'):await self.call(client,'capture','camera.meter')
            with self.assertRaisesRegex(ValueError,'not configured'):await self.call(client,'state','camera.other')
            await self.call(client,'light_on','light.meter');self.assertEqual(self.light,'on')
            self.assertTrue(self.gateway.pending)
        self.assertEqual(self.light,'off');self.assertFalse(self.gateway.pending)
        self.assertFalse(Path(access['socket']).exists())
    async def test_warmup_allows_three_captures_but_not_four(self):
        settings={'capture':{**self.settings['capture'],'discard_first':True}}
        async with self.gateway.session(settings) as access:
            client=HomeAssistantClient(access)
            for _ in range(3):self.assertEqual(await self.call(client,'capture','camera.meter'),PNG)
            with self.assertRaisesRegex(ValueError,'three camera'):await self.call(client,'capture','camera.meter')

    async def test_failure_and_cancel_restore_light(self):
        for error in (RuntimeError,asyncio.CancelledError):
            with self.assertRaises(error):
                async with self.gateway.session(self.settings) as access:
                    await self.call(HomeAssistantClient(access),'light_on','light.meter')
                    raise error()
            self.assertEqual(self.light,'off');self.assertFalse(self.gateway.in_use)
    async def test_already_on_light_untouched_and_concurrent_access_rejected(self):
        self.light='on'
        async with self.gateway.session(self.settings) as access:
            await self.call(HomeAssistantClient(access),'light_on','light.meter')
            async with self.gateway.session(self.settings) as other:
                with self.assertRaisesRegex(ValueError,'busy'):await self.call(HomeAssistantClient(other),'state','camera.meter')
            self.assertIn('camera.meter',self.gateway.in_use)
        self.assertEqual(self.light,'on');self.assertFalse(any(path.startswith('services') for _,path in self.calls))
    async def test_restore_failure_durable_recovery_and_connection_pin(self):
        with self.assertRaisesRegex(Invalid,'restoration is pending'):
            async with self.gateway.session(self.settings) as access:
                await self.call(HomeAssistantClient(access),'light_on','light.meter');self.fail_restore=True
        other=HomeAssistant(self.root)
        self.assertIn('light.meter',other.pending)
        with patch.dict(os.environ,{'HA_URL':'http://different.invalid'}):
            with self.assertRaisesRegex(Invalid,'different'):await other.restore('light.meter')
        self.fail_restore=False;await other.restore('light.meter')
        self.assertFalse(other.pending);self.assertEqual(self.light,'off')
    async def test_failed_persistence_does_not_switch_light(self):
        async with self.gateway.session(self.settings) as access:
            with patch.object(self.gateway.store,'write',side_effect=OSError('private path')):
                with self.assertRaisesRegex(ValueError,'operation failed'):await self.call(HomeAssistantClient(access),'light_on','light.meter')
        self.assertEqual(self.light,'off');self.assertFalse(any(path.startswith('services') for _,path in self.calls))
    async def test_real_runner_client_and_timeout_cleanup(self):
        source=self.root/'source';source.mkdir()
        (source/'action.py').write_text("import time\ndef execute(data, settings, *, context):\n h=context['home_assistant'];h.light_on('light.meter');time.sleep(20)\n")
        async with self.gateway.session(self.settings) as access:
            request={'mode':'execute','data':{},'settings':self.settings,'capabilities':['home_assistant'],'context':{'home_assistant':access}}
            with self.assertRaises(asyncio.TimeoutError):
                await process([sys.executable,'-I',RUNNER,source,'action.py'],cwd=source,payload=json.dumps(request).encode(),capture=True,timeout=.5)
        self.assertIn(('POST','services/light/turn_on'),self.calls)
        self.assertEqual(self.light,'off');self.assertFalse(self.gateway.pending)
    async def test_missing_connection_and_supervisor_selection(self):
        with patch.dict(os.environ,{'HA_URL':'','HA_ACCESS_TOKEN':''}):self.assertIsNone(connection())
        with patch.dict(os.environ,{'HA_AI_STUDIO_DEV':'0','SUPERVISOR_TOKEN':'test-supervisor'}):
            self.assertEqual(connection(),('http://supervisor/core/api','test-supervisor'))

    async def test_real_runner_preview_receives_no_device_client(self):
        from app.plugins import Environment
        from app.store import Store
        from types import SimpleNamespace
        source=self.root/'source';source.mkdir()
        (source/'action.py').write_text("def execute(data, settings, *, context): return {}\ndef preview(data, settings, *, context): assert 'home_assistant' not in context;return {'isolated':True}\n")
        plugin=SimpleNamespace(manifest={'capabilities':['home_assistant','preview'],'entrypoint':'action.py','output_schema':{}},record={'commit':'test'})
        class FixtureEnvironment(Environment):
            @property
            def path(inner):return self.root
            def require(inner):pass
        (self.root/'bin').mkdir();(self.root/'bin/python').symlink_to(sys.executable)
        environment=object.__new__(FixtureEnvironment);environment.plugin=plugin;environment.store=Store(self.root);environment.home_assistant=self.gateway
        result=await environment.preview({},self.settings,self.root)
        self.assertEqual(result,{'isolated':True});self.assertEqual(self.calls,[]);self.assertFalse(self.gateway.in_use)
        (source/'action.py').write_text("def execute(data, settings, *, context):\n h=context['home_assistant'];h.light_on('light.meter');return {'captured':bool(h.capture('camera.meter'))}\ndef preview(data, settings, *, context):return {}\n")
        from app.test_progress import current
        entries=[];token=current.set(entries.append)
        try:result=await environment.invoke({},self.settings,'camera_test',self.root)
        finally:current.reset(token)
        self.assertTrue(any('Fetching camera image' in entry for entry in entries))
        self.assertTrue(any('restoring illumination' in entry for entry in entries))
        self.assertEqual(result,{'captured':True});self.assertEqual(self.light,'off');self.assertFalse(self.gateway.pending)
        self.assertIn(('GET','camera_proxy/camera.meter'),self.calls)
    async def test_cancel_during_inflight_light_call_restores_after_completion(self):
        original=self.gateway.service;started=asyncio.Event();finish=asyncio.Event()
        async def delayed(entity,operation,**data):
            if operation=='turn_on':started.set();await finish.wait()
            await original(entity,operation,**data)
        async def execution():
            async with self.gateway.session(self.settings) as access:
                await self.call(HomeAssistantClient(access),'light_on','light.meter')
        with patch.object(self.gateway,'service',side_effect=delayed):
            task=asyncio.create_task(execution());await started.wait();task.cancel();finish.set()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual([path for _,path in self.calls if path.startswith('services/')],['services/light/turn_on','services/light/turn_off'])
        self.assertFalse(self.gateway.pending);self.assertEqual(self.light,'off')
    async def test_state_only_session_does_not_reserve_until_used(self):
        async with self.gateway.session(self.settings):self.assertFalse(self.gateway.in_use)
    async def test_restore_journal_clear_failure_keeps_recovery_record(self):
        with self.assertRaisesRegex(Invalid,'restoration is pending'):
            async with self.gateway.session(self.settings) as access:
                await self.call(HomeAssistantClient(access),'light_on','light.meter')
                with patch.object(self.gateway.store,'write',side_effect=OSError()):
                    with self.assertRaises(ValueError):await self.call(HomeAssistantClient(access),'restore','light.meter')
                self.fail_restore=True
        self.assertIn('light.meter',self.gateway.pending)
        self.fail_restore=False;await self.gateway.restore('light.meter');self.assertFalse(self.gateway.pending)
    async def test_pending_restoration_blocks_device_calls(self):
        self.gateway.pending={'light.meter':{'connection':self.gateway.fingerprint()}}
        async with self.gateway.session(self.settings) as access:
            with self.assertRaisesRegex(ValueError,'restoration'):await self.call(HomeAssistantClient(access),'capture','camera.meter')
        self.assertEqual(self.calls,[])
    async def test_api_errors_are_sanitized_and_redirects_not_followed(self):
        async def rejected(request):return web.Response(status=int(request.match_info['status']),text='private-test-token',headers={'Location':'http://different.invalid/secret'})
        app=web.Application();app.router.add_get('/api/{status}',rejected)
        server=TestServer(app);await server.start_server()
        try:
            with patch.dict(os.environ,{'HA_URL':str(server.make_url('')).rstrip('/')}):
                for status in (302,401,403,404,500,200):
                    with self.subTest(status=status),self.assertRaises(Invalid) as caught:await self.gateway.request('GET',str(status))
                    self.assertNotIn('private-test-token',str(caught.exception));self.assertNotIn('different.invalid',str(caught.exception))
        finally:await server.close()

    async def test_wait_events_log_exact_delays_without_device_requests(self):
        from app.test_progress import current
        entries=[];token=current.set(entries.append)
        try:
            async with self.gateway.session(self.settings) as access:
                client=HomeAssistantClient(access)
                await self.call(client,'report_wait','camera.meter','before_capture',2)
                await self.call(client,'report_wait','camera.meter','between_captures',5.5)
                with self.assertRaises(ValueError):await self.call(client,'report_wait','camera.meter','before_capture',21)
        finally:current.reset(token)
        self.assertIn('Waiting 2 seconds before the first capture.',entries)
        self.assertIn('Waiting 5.5 seconds before the next capture.',entries)
        self.assertEqual(self.calls,[])

    async def test_dimmable_light_full_brightness_and_restore(self):
        for state in ('on','off'):
            self.light=state;self.brightness=73
            async with self.gateway.session(self.settings) as access:
                client=HomeAssistantClient(access)
                await self.call(client,'light_on','light.meter')
                self.assertEqual(self.brightness,255)
                await self.call(client,'light_on','light.meter')
                self.assertEqual(self.gateway.pending['light.meter']['brightness'],73)
            self.assertEqual(self.light,state)
            self.assertEqual(self.brightness,73)
            self.assertFalse(self.gateway.pending)

    async def test_brightness_restoration_survives_restart(self):
        self.light='on';self.brightness=255
        self.gateway.pending={'light.meter':{'connection':self.gateway.fingerprint(),'state':'on','brightness':42}}
        self.gateway.store.write('restore.json',self.gateway.pending)
        restored=HomeAssistant(self.root)
        await restored.restore('light.meter')
        self.assertEqual(self.light,'on');self.assertEqual(self.brightness,42)
        self.assertFalse(restored.pending)

    async def test_off_dimmable_without_saved_brightness_uses_full_brightness(self):
        self.color_modes=['brightness']
        async with self.gateway.session(self.settings) as access:
            await self.call(HomeAssistantClient(access),'light_on','light.meter')
            self.assertEqual(self.service_data[-1],{'entity_id':'light.meter','brightness':255})
        self.assertEqual(self.light,'off');self.assertFalse(self.gateway.pending)

    async def test_on_dimmable_unknown_brightness_rejected_before_mutation(self):
        self.light='on';self.color_modes=['brightness']
        async with self.gateway.session(self.settings) as access:
            with self.assertRaisesRegex(ValueError,'brightness is unavailable'):
                await self.call(HomeAssistantClient(access),'light_on','light.meter')
        self.assertEqual(self.service_data,[]);self.assertFalse(self.gateway.pending)

    async def test_brightness_restore_failure_is_durable_and_blocks_capture(self):
        self.light='on';self.brightness=73
        with self.assertRaisesRegex(Invalid,'restoration is pending'):
            async with self.gateway.session(self.settings) as access:
                await self.call(HomeAssistantClient(access),'light_on','light.meter')
                self.fail_restore=True
        restored=HomeAssistant(self.root)
        self.assertEqual(restored.pending['light.meter']['brightness'],73)
        async with restored.session(self.settings) as access:
            with self.assertRaisesRegex(ValueError,'restoration'):
                await self.call(HomeAssistantClient(access),'capture','camera.meter')
        self.fail_restore=False
        await restored.restore('light.meter')
        self.assertEqual(self.light,'on');self.assertEqual(self.brightness,73)
        self.assertFalse(restored.pending)
