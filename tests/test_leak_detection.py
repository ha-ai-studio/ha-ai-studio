"""Synthetic history only; no production Home Assistant access."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import tempfile
import asyncio
from app.home_assistant import HomeAssistant
from app.ha_action_client import HomeAssistantClient
from app.domain import Invalid
from app.plugins import schema_valid

ROOT = Path(__file__).resolve().parents[1] / 'local-actions/actions/water-leak-detection'
leak = None
if ROOT.is_dir():
    spec = importlib.util.spec_from_file_location('leak_action', ROOT / 'action.py')
    leak = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(leak)
SOURCE_REQUIRED = 'Set STUDIO_LOCAL_ACTIONS_DIR to the edge-meter repository to test Leak detection.'
SETTINGS = {'entity_id':'sensor.water', 'history_hours':8, 'minimum_rate_lph':1,
            'minimum_duration_hours':6, 'max_gap_minutes':60}


def history(values=None):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = values if values is not None else [100 + i * .002 for i in range(9)]
    return {'start':start.isoformat(), 'end':(start+timedelta(hours=8)).isoformat(),
            'readings':[{'state':str(v), 'timestamp':(start+timedelta(hours=i)).isoformat(),
                         'unit':'m³', 'state_class':'total_increasing'} for i,v in enumerate(values)]}


@unittest.skipUnless(leak is not None, SOURCE_REQUIRED)
class AnalysisTests(unittest.TestCase):
    def test_sustained_and_normal(self):
        value=leak.analyze(history(), SETTINGS)
        self.assertEqual(value['status'], 'suspected_leak')
        self.assertEqual(value['total_liters'], 16)
        self.assertEqual(leak.analyze(history([100]*9), SETTINGS)['status'],'normal')
        self.assertEqual(leak.analyze(history([100,100,101,101,102,102,103,103,104]), SETTINGS)['status'],'normal')
    def test_invalid_data_never_means_normal(self):
        for change in ('reset','unknown','nan','gap','duplicate','unit','class','future','stale','negative','timezone','overflow'):
            with self.subTest(change=change):
                value=history();row=value['readings'][4]
                if change=='reset':row['state']='0'
                if change in ('unknown','nan'):row['state']=change
                if change=='negative':row['state']='-1'
                if change=='overflow':row['state']='1e999999999'
                if change=='gap':value['readings'].pop(4)
                if change=='duplicate':row['timestamp']=value['readings'][3]['timestamp']
                if change=='unit':row['unit']='L'
                if change=='class':row['state_class']='measurement'
                if change=='future':row['timestamp']='2099-01-01T00:00:00+00:00'
                if change=='stale':value['readings']=value['readings'][:-2]
                if change=='timezone':row['timestamp']='2026-01-01T04:00:00'
                self.assertEqual(leak.analyze(value,SETTINGS)['status'],'insufficient_data')
    def test_exact_threshold_decimal_readings(self):
        value=history()
        for i,row in enumerate(value['readings']):row['state']=f'{1+i/1000:.6f}'
        self.assertEqual(leak.analyze(value,SETTINGS)['status'],'suspected_leak')
        value['readings'][4]['timestamp']=None
        self.assertEqual(leak.analyze(value,SETTINGS)['status'],'insufficient_data')

    def test_reading_ids_can_bind_to_device(self):
        from app.devices import identifier
        for row in leak.analyze(history(),SETTINGS)['readings']:
            self.assertTrue(identifier(row['id']))

    def test_missing_and_short_history(self):
        for readings in ([], history()['readings'][:3]):
            value=history();value['readings']=readings
            self.assertEqual(leak.analyze(value,SETTINGS)['status'],'insufficient_data')
    def test_liters_and_duration(self):
        value=history()
        for row in value['readings']:row['unit']='L';row['state']=str(float(row['state'])*1000)
        self.assertEqual(leak.analyze(value,SETTINGS)['status'],'suspected_leak')
        self.assertEqual(leak.analyze(value,{**SETTINGS,'minimum_duration_hours':9})['status'],'insufficient_data')
    def test_api_failure_safe_result(self):
        class Client:
            def history(self,*args):raise ValueError('private-token')
        value=leak.execute({},SETTINGS,context={'home_assistant':Client()})
        self.assertEqual(value['status'],'insufficient_data')
        self.assertNotIn('private-token',json.dumps(value))


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.gateway=HomeAssistant(self.tmp.name)
    async def asyncTearDown(self):self.tmp.cleanup()
    async def test_scope_bounds_and_sanitized_rpc(self):
        row={'entity_id':'sensor.water','state':'12','last_updated':'2026-01-01T00:00:00+00:00',
             'attributes':{'unit_of_measurement':'m³','state_class':'total_increasing','secret':'never-forward'},'context':{'secret':'never-forward'}}
        self.gateway.request=AsyncMock(return_value=[[row]*200])
        async with self.gateway.session(SETTINGS) as access:
            client=HomeAssistantClient(access)
            value=await asyncio.to_thread(client.history,'sensor.water',8)
            self.assertEqual(len(value['readings']),200) # Exceeds the old 16 KiB RPC limit.
            self.assertNotIn('never-forward',json.dumps(value))
            path=self.gateway.request.call_args.args[1]
            self.assertIn('filter_entity_id=sensor.water',path)
            self.assertIn('skip_initial_state=1',path)
            for entity,hours in [('sensor.other',8),('camera.other',8),('sensor.water',0),('sensor.water',169),('sensor.water',True)]:
                with self.assertRaises(ValueError):await asyncio.to_thread(client.history,entity,hours)
    async def test_invalid_responses(self):
        for raw in ({}, [[{'entity_id':'sensor.other'}]], [[]]*2, [[{}]*10001]):
            self.gateway.request=AsyncMock(return_value=raw)
            with self.assertRaises(Invalid):await self.gateway.history('sensor.water',24)
    @unittest.skipUnless(leak is not None, SOURCE_REQUIRED)
    async def test_manifest_and_result_contract(self):
        manifest=json.loads((ROOT/'action.json').read_text())
        self.assertTrue(await schema_valid(manifest['settings_schema'],SETTINGS))
        self.assertFalse(await schema_valid(manifest['settings_schema'],{**SETTINGS,'entity_id':'light.water'}))
        for value in (history(),history([])):
            self.assertTrue(await schema_valid(manifest['output_schema'],leak.analyze(value,SETTINGS)))

    @unittest.skipUnless(leak is not None, SOURCE_REQUIRED)
    async def test_snapshot_runner_and_device_function(self):
        import os
        from app.actions import Studio
        with patch.dict(os.environ, {'HA_AI_STUDIO_DEV':'1','HA_AI_STUDIO_LOCAL_ACTIONS':str(ROOT)}):
            studio=Studio(Path(self.tmp.name)/'studio')
            try:
                repositories=await studio.repositories.save({'kind':'local','path':'.'})
                repository=repositories['repositories'][0]['id']
                await studio.repositories.tasks[repository]
                kind=next(key for key, plugin in studio.repositories.types().items() if plugin.manifest['id']=='water-leak-detection')
                await studio.create({'id':'leak','name':'Leak detection','type':kind})
                action=studio.get('leak');await action.environment.task
                self.assertTrue(action.environment.ready, action.environment.state)
                config=action.config['draft'];config['settings']=dict(SETTINGS)
                await action.save(config,False)
                end=datetime.now(timezone.utc)-timedelta(seconds=1)
                rows=[{'entity_id':'sensor.water','state':f'{1+i/1000:.6f}',
                       'last_updated':(end-timedelta(minutes=30*(15-i))).isoformat(),
                       'attributes':{'unit_of_measurement':'m³','state_class':'total_increasing'}} for i in range(16)]
                studio.home_assistant.request=AsyncMock(return_value=[rows])
                item=studio.devices.create({'name':'Water meter'})
                device_config={**item['draft'], 'sensors':[{'id':'leak-status','name':'Leak status',
                    'action_id':'leak','reading':'leak-status','device_class':'','unit':'','cumulative':False,'max_change':None}],
                    'functions':[{'id':'check-leaks','name':'Check for leaks','action_id':'leak','function':'execute'}]}
                await studio.devices.save(item['id'],{'config':device_config,'enabled':True})
                schedule=studio.devices.get(item['id'])['next_run']
                studio.devices.invoke_function(item['id'],{'function_id':'check-leaks','request_id':'check-1'})
                await studio.devices.tasks[item['id']]
                device=studio.devices.get(item['id'])
                self.assertEqual(device['values']['leak-status']['value'],'suspected_leak')
                self.assertEqual(device['next_run'],schedule)
                rows[5]['state']='unavailable'
                studio.devices.invoke_function(item['id'],{'function_id':'check-leaks','request_id':'check-2'})
                await studio.devices.tasks[item['id']]
                self.assertEqual(studio.devices.get(item['id'])['values']['leak-status']['value'],'insufficient_data')
            finally:
                await studio.close()
