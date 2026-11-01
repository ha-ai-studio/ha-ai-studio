"""Explicit device functions: durable invocation, selection and discovery lifecycle."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch
from app.actions import Studio
from app.domain import Invalid
from app.discovery import DEFAULT
from test_actions import broker
import test_devices
from test_devices import configured_device, sensor, reading


def function(id='read-water', action='reader'):
    return {'id': id, 'name': 'Read water', 'action_id': action, 'function': 'execute'}


class DeviceFunctionTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = test_devices.DeviceTests.asyncTearDown
    # Reuse setup without duplicating inherited tests in this class.
    async def asyncSetUp(self):
        await test_devices.DeviceTests.asyncSetUp(self)
        self.declaration = patch.dict(self.action.plugin.metadata, {'functions': ['execute']})
        self.declaration.start();self.addCleanup(self.declaration.stop)

    async def device(self, sensors=None, functions=None, publish=False):
        item = self.manager.create({'name': 'Function device'})
        config = {**item['draft'], 'sensors': sensors or [], 'functions': functions or [function()], 'publish': publish}
        return await self.manager.save(item['id'], {'config': config, 'enabled': True})

    async def test_function_targets_one_action_updates_only_its_sensors_and_preserves_schedule(self):
        item = await self.device([sensor(), sensor('other', action='other')])
        before = self.manager.get(item['id'])['next_run']
        with patch.object(self.action, 'execute', AsyncMock(return_value={'readings': [reading()]})) as execute, patch.object(self.studio.get('other'), 'execute', AsyncMock()) as other:
            request = {'function_id': 'read-water', 'request_id': 'unique'}
            self.manager.invoke_function(item['id'], request)
            self.manager.invoke_function(item['id'], request)
            await self.manager.tasks[item['id']]
            self.manager.invoke_function(item['id'], request)
            execute.assert_awaited_once();other.assert_not_awaited()
        state = self.manager.get(item['id'])
        self.assertEqual(set(state['values']), {'water'})
        self.assertEqual(state['next_run'], before)
        self.assertNotIn('function_requests', self.manager.public(item['id']))
        self.assertIn('binding', state['function_requests']['unique'])
        restored = Studio(self.tmp.name)
        restored.devices.invoke_function(item['id'], request)
        self.assertFalse(restored.devices.tasks)
        await restored.close()

    async def test_function_only_devices_are_not_scheduled_and_hold_action_reference(self):
        item = await self.device()
        self.assertIsNone(item['next_run'])
        self.assertTrue(self.manager.references('reader', active_only=True))
        with self.assertRaises(Invalid):await self.studio.delete({'id':'reader','confirmed':True})
        with patch.object(self.action, 'execute', AsyncMock(return_value={'readings': []})) as execute:
            self.manager.invoke_function(item['id'], {'function_id':'read-water','request_id':'only'})
            await self.manager.tasks[item['id']]
            execute.assert_awaited_once()
        self.assertIsNone(self.manager.get(item['id'])['next_run'])
        self.assertEqual(self.manager.get(item['id'])['status'], 'ready')

    async def test_function_validation_drafts_conflicts_disabled_and_persistence_failure(self):
        item = await self.device(functions=[function(),function('another')])
        config = copy.deepcopy(item['draft'])
        for invalid in [None, {}, [function(), function()], [{**function(), 'function':'unknown'}], [{**function(), 'publish':1}], [{**function(), 'id':'../bad'}]]:
            with self.assertRaises(Invalid):await self.manager.save(item['id'], {'config':{**config,'functions':invalid},'enabled':True})
        request = {'function_id':'read-water','request_id':'one'}
        with patch.object(self.manager.store, 'write', side_effect=OSError()):
            with self.assertRaises(OSError):self.manager.invoke_function(item['id'],request)
        self.assertNotIn('one',self.manager.get(item['id'])['function_requests'])
        self.assertFalse(self.manager.tasks)
        with patch.object(self.action, 'execute', AsyncMock(return_value={'readings': []})):
            self.manager.invoke_function(item['id'],request)
            await self.manager.tasks[item['id']]
        with self.assertRaises(Invalid):self.manager.invoke_function(item['id'],{'function_id':'another','request_id':'one'})
        for value in (None, [], False, 2, '', ' ', '\n', 'a'*129):
            with self.assertRaises(Invalid):self.manager.invoke_function(item['id'],{'function_id':'read-water','request_id':value})
        config['functions'][0]['id']='draft-only'
        await self.manager.save(item['id'], {'config':config,'activate':False})
        with self.assertRaises(Invalid):self.manager.invoke_function(item['id'],{'function_id':'draft-only','request_id':'two'})
        self.manager.deactivate(item['id'])
        with self.assertRaises(Invalid):self.manager.invoke_function(item['id'],{'function_id':'read-water','request_id':'two'})

    async def test_interrupted_function_does_not_replay(self):
        item = await self.device()
        entered=asyncio.Event()
        async def execute(*args):
            entered.set();await asyncio.Future()
        request={'function_id':'read-water','request_id':'interrupted'}
        with patch.object(self.action, 'execute', execute):
            self.manager.invoke_function(item['id'],request)
            await entered.wait()
            task=self.manager.tasks[item['id']];task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        restored=Studio(self.tmp.name)
        self.assertEqual(restored.devices.get(item['id'])['status'],'interrupted')
        restored.devices.invoke_function(item['id'],request)
        self.assertFalse(restored.devices.tasks)
        self.assertIsNone(restored.devices.get(item['id'])['next_run'])
        await restored.close()

    async def test_discovery_opt_in_identity_routes_cleanup_and_commands(self):
        broker_id=(await self.studio.brokers.save(broker()))['brokers'][0]['id']
        discovery=self.studio.discovery
        await discovery.save({**DEFAULT,'enabled':True,'broker_id':broker_id})
        item=await self.device(functions=[function(),function('second')],publish=True)
        topic=discovery.command_topic(self.manager.get(item['id']), function())
        button=f"homeassistant/button/{item['identity']}/function_read-water/config"
        desired=discovery.desired()
        self.assertEqual(desired[button]['device']['identifiers'],[item['identity']])
        self.assertEqual(desired[button]['command_topic'],topic)
        self.assertFalse(desired[button]['retain'])
        self.assertEqual(desired[button]['qos'],1)
        self.assertEqual(len(discovery.command_routes()),2)
        self.assertNotIn(topic,desired) # Commands must never be published as retained state.
        route={'input_broker':broker_id,'output_broker':broker_id,'request_topic':topic,'result_topic':'safe/result'}
        with self.assertRaises(Invalid):discovery.check_routes(route)
        with patch.object(self.action,'execute',AsyncMock(return_value={'readings':[]})) as execute:
            for payload in [b'null',b'[]',b'{"status":"success"}',b'{"request_id":"x","data":{}}']:
                discovery.receive_command(broker_id,topic,payload)
            discovery.receive_command('wrong-broker',topic,b'{"request_id":"x"}')
            self.assertFalse(self.manager.tasks)
            discovery.receive_command(broker_id,topic,b'{"request_id":"x"}')
            await self.manager.tasks[item['id']]
            execute.assert_awaited_once()
        with patch('app.discovery.MQTT') as transport:
            await discovery.reconcile()
            self.manager.deactivate(item['id'])
            await discovery.reconcile()
            self.assertEqual(discovery.desired()[item['identity']+'/function_availability'],'offline')
            self.assertEqual(discovery.command_routes(enabled_only=True),{})
            self.manager.delete({'id':item['id'],'confirmed':True})
            await discovery.reconcile()
            self.assertTrue(any(c.args[:2]==(button,'') for c in transport.return_value.publish.call_args_list))

    async def test_old_devices_migrate_without_functions_or_identity_changes(self):
        item=await configured_device(self.studio)
        data=copy.deepcopy(self.manager.data)
        for config in (data['items'][item['id']]['draft'],data['items'][item['id']]['active']):config.pop('functions')
        data['items'][item['id']].pop('function_requests')
        self.manager.store.write('devices.json',data)
        restored=Studio(self.tmp.name)
        current=restored.devices.get(item['id'])
        self.assertEqual(current['identity'],item['identity'])
        self.assertEqual(current['active']['functions'],[])
        self.assertEqual(current['function_requests'],{})
        await restored.close()

    async def test_function_request_history_is_bounded_without_evicting_recent_dedup(self):
        item=await self.device()
        with patch('app.devices.MAX_FUNCTION_REQUESTS', 2), patch.object(self.action,'execute',AsyncMock(return_value={'readings':[]})) as execute:
            for request_id in ('one','two','three'):
                self.manager.invoke_function(item['id'],{'function_id':'read-water','request_id':request_id})
                await self.manager.tasks[item['id']]
            self.assertEqual(list(self.manager.get(item['id'])['function_requests']),['two','three'])
            self.manager.invoke_function(item['id'],{'function_id':'read-water','request_id':'two'})
            self.assertEqual(execute.await_count,3)

    async def test_activation_rejects_existing_ha_birth_topic_collision(self):
        broker_id=(await self.studio.brokers.save(broker()))['brokers'][0]['id']
        item=self.manager.create({'name':'Conflict'})
        topic=self.studio.discovery.command_topic(item,function())
        await self.studio.discovery.save({**DEFAULT,'enabled':True,'broker_id':broker_id,'ha_status_topic':topic})
        with self.assertRaisesRegex(Invalid,'status topic'):
            await self.manager.save(item['id'],{'config':{**item['draft'],'functions':[function()],'publish':True},'enabled':True})
        self.assertIsNone(self.manager.get(item['id'])['active'])

    async def test_function_without_sensors_accepts_arbitrary_action_output(self):
        item=await self.device()
        for i, output in enumerate([None, False, 0, 'done', [], {'result':'ok'}]):
            with patch.object(self.action,'execute',AsyncMock(return_value=output)):
                self.manager.invoke_function(item['id'],{'function_id':'read-water','request_id':str(i)})
                await self.manager.tasks[item['id']]
            self.assertEqual(self.manager.get(item['id'])['status'],'ready')

    async def test_function_publication_follows_device_and_migrates_old_flags(self):
        item=await self.device(functions=[function(),function('second')])
        discovery=self.studio.discovery
        self.assertEqual(discovery.command_routes(),{})
        self.assertFalse(any('/button/' in topic for topic in discovery.desired()))
        broker_id=(await self.studio.brokers.save(broker()))['brokers'][0]['id']
        await discovery.save({**DEFAULT,'enabled':True,'broker_id':broker_id})
        config={**item['draft'],'publish':True}
        await self.manager.save(item['id'],{'config':config,'enabled':True})
        expected=discovery.desired()
        self.assertEqual(len([topic for topic in expected if '/button/' in topic]),2)
        legacy=copy.deepcopy(self.manager.data)
        for saved in (legacy['items'][item['id']]['draft'],legacy['items'][item['id']]['active']):
            saved['functions'][0]['publish']=False
            saved['functions'][1]['publish']=True
        self.manager.store.write('devices.json',legacy)
        restored=Studio(self.tmp.name)
        self.assertEqual(restored.discovery.desired(),expected)
        self.assertTrue(all('publish' not in f for f in restored.devices.get(item['id'])['active']['functions']))
        await restored.close()
        with patch('app.discovery.MQTT') as transport:
            await discovery.reconcile()
            await self.manager.save(item['id'],{'config':{**config,'publish':False},'enabled':True})
            # A queued callback must be rejected before transport reconciliation.
            topic=discovery.command_topic(self.manager.get(item['id']),function())
            discovery.receive_command(broker_id,topic,b'{"request_id":"queued-after-unpublish"}')
            self.assertFalse(self.manager.tasks)
            with self.assertRaises(Invalid):
                self.manager.invoke_function(item['id'],{'function_id':'read-water','request_id':'remote-after-unpublish'},remote=True)
            await discovery.reconcile()
            self.assertEqual(discovery.command_routes(),{})
            for topic in expected:
                if '/button/' in topic:self.assertTrue(any(c.args[:2]==(topic,'') for c in transport.return_value.publish.call_args_list))

    async def test_execute_updates_dependent_rates_only_and_keeps_schedule(self):
        rate = test_devices.DeviceTests.rate_sensor(self)
        other_rate = {**rate, 'id':'other-rate', 'rate':{**rate['rate'], 'source':'other'}}
        item = await self.device([sensor(), rate, sensor('other', action='other'), other_rate])
        schedule = self.manager.get(item['id'])['next_run']
        for request_id, value, at in [('first',100,1000), ('second',101,1060)]:
            with patch.object(self.action, 'execute', AsyncMock(return_value={'readings':[{**reading(value=value),'measured_at':at}]})), patch.object(self.studio.get('other'), 'execute', AsyncMock()) as other:
                self.manager.invoke_function(item['id'], {'function_id':'read-water','request_id':request_id})
                await self.manager.tasks[item['id']]
                other.assert_not_awaited()
        state = self.manager.get(item['id'])
        self.assertEqual(state['status'], 'ready')
        self.assertAlmostEqual(state['values']['flow']['value'], 1000)
        self.assertNotIn('other-rate', state['values'])
        self.assertEqual(state['next_run'], schedule)
