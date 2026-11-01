import asyncio
import copy
import json
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from app.actions import Studio
from app.action_types import TYPES
from app.domain import Invalid
from app.discovery import DEFAULT
from test_actions import broker


def sensor(id='water', action='reader', kind='water', unit='m³'):
    return {'id': id, 'name': id.title(), 'device_class': '' if kind == 'number' else kind, 'cumulative': kind == 'water', 'unit': unit, 'action_id': action, 'reading': id, 'max_change': None}


async def configured_device(studio, sensors=None, publish=False, name='Water meter'):
    item = studio.devices.create({'name': name})
    config = copy.deepcopy(item['draft'])
    config.update(sensors=sensors or [sensor()], publish=publish)
    return await studio.devices.save(item['id'], {'config': config, 'activate': True})


def reading(id='water', value=123, unit='m³', status='read'):
    return {'id': id, 'value': value, 'unit': unit, 'status': status}


class DeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        catalog = patch('app.actions.CATALOG', [TYPES['echo'].metadata]);catalog.start();self.addCleanup(catalog.stop)
        self.studio = Studio(self.tmp.name)
        for id in ('reader', 'other'):
            await self.studio.create({'id': id, 'name': id, 'type': 'echo'})
        self.manager = self.studio.devices
        self.action = self.studio.get('reader')

    async def asyncTearDown(self):
        await self.studio.close();self.tmp.cleanup()

    async def run_device(self, id, readings):
        with patch.object(self.action, 'execute', AsyncMock(return_value={'readings': readings})) as execute:
            self.manager.start_read(id)
            await self.manager.tasks[id]
            return execute

    async def test_live_measurement_log_is_scoped_bounded_and_reset(self):
        from app.test_progress import current
        item = await configured_device(self.studio)
        other = await configured_device(self.studio, name='Other device')
        entered, release = asyncio.Event(), asyncio.Event()
        async def execute(data, settings):
            current.get()('Capturing camera image.')
            entered.set()
            await release.wait()
            return {'readings': [reading()]}
        with patch.object(self.action, 'execute', execute):
            self.manager.start_read(item['id'])
            await entered.wait()
            logs = self.manager.public(item['id'])['logs']
            self.assertEqual(logs[-1]['message'], 'Capturing camera image.')
            self.assertEqual(self.manager.public(other['id'])['logs'], [])
            release.set()
            await self.manager.tasks[item['id']]
        self.assertEqual(self.manager.public(item['id'])['logs'][-1]['message'], 'Measurement finished: ready.')
        self.assertIsNone(current.get())
        for _ in range(250):self.manager.log(item['id'], 'Old entry.')
        self.assertEqual(len(self.manager.public(item['id'])['logs']), 200)
        await self.run_device(item['id'], [reading()])
        self.assertNotIn('Old entry.', [e['message'] for e in self.manager.public(item['id'])['logs']])

    async def test_internal_execution_once_for_multiple_sensors_no_mqtt(self):
        item = await configured_device(self.studio, [sensor(), sensor('temperature', kind='temperature', unit='°C')])
        with patch('app.actions.MQTT') as transport:
            execute = await self.run_device(item['id'], [reading(), reading('temperature', 20, '°C')])
            transport.assert_not_called();execute.assert_awaited_once()
        result = self.manager.public(item['id'])
        self.assertEqual(result['status'], 'ready');self.assertEqual(result['values']['water']['value'], 123)
        self.assertFalse(self.action.enabled)
        self.assertEqual(self.action.last['execution'], 'success')
        self.assertEqual(next(iter(self.action.jobs.values()))['source'], 'device')
        self.assertEqual(next(iter(self.action.jobs.values()))['execution'], 'success')

    async def test_concrete_reading_errors_and_diagnostic_messages(self):
        broker_id = (await self.studio.brokers.save(broker()))['brokers'][0]['id']
        await self.studio.discovery.save({**DEFAULT, 'enabled': True, 'broker_id': broker_id})
        item = await configured_device(self.studio, [sensor(), sensor('serial', kind='number', unit='')], publish=True)
        await self.run_device(item['id'], [reading(), reading('serial', '001234', '')])
        alignment_error = 'Alignment reference was not found confidently.'
        with patch.object(self.action, 'execute', AsyncMock(return_value={
                'status': 'unreadable', 'message': alignment_error, 'readings': []})):
            self.manager.start_read(item['id'])
            await self.manager.tasks[item['id']]
        for key in ('water', 'serial'):
            self.assertEqual(self.manager.get(item['id'])['values'][key]['error'], alignment_error)
            self.assertEqual(self.studio.discovery.desired()[item['identity']+'/status_message_'+key], alignment_error)
        self.assertEqual(self.manager.get(item['id'])['values']['water']['value'], 123)
        reason = 'Frame test-frame: Model fit is below the configured threshold.'
        await self.run_device(item['id'], [{**reading(status='unreadable', value=None), 'reason': reason}, reading('serial', '001234', '')])
        desired = self.studio.discovery.desired()
        self.assertEqual(desired[item['identity']+'/status_message_water'], reason)
        self.assertEqual(desired[item['identity']+'/status_message_serial'], 'Reading successful.')
        entity = desired['homeassistant/sensor/'+item['identity']+'/status_message_water/config']
        self.assertEqual(entity['entity_category'], 'diagnostic')
        self.assertEqual(entity['name'], 'Water Status Message')
        restored = Studio(self.tmp.name)
        self.assertEqual(restored.devices.get(item['id'])['values']['water']['error'], reason)
        await restored.close()
        await self.run_device(item['id'], [{**reading(status='unreadable', value=None), 'reason': 'x'*300}, reading('serial', '001234', '')])
        self.assertEqual(len(self.studio.discovery.desired()[item['identity']+'/status_message_water']), 255)
        self.assertEqual(len(self.manager.get(item['id'])['values']['water']['error']), 300)
        await self.run_device(item['id'], [reading(), reading('serial', '001234', '')])
        self.assertEqual(self.studio.discovery.desired()[item['identity']+'/status_message_water'], 'Reading successful.')

    async def test_independent_failure_and_preserved_value(self):
        item = await configured_device(self.studio, [sensor(), sensor('temperature', 'other', 'temperature', '°C')])
        other = self.studio.get('other')
        with patch.object(other, 'execute', AsyncMock(return_value={'readings': [reading('temperature', 20, '°C')]})):
            await self.run_device(item['id'], [reading()])
        before = copy.deepcopy(self.manager.get(item['id'])['values']['water'])
        with patch.object(other, 'execute', AsyncMock(return_value={'readings': [reading('temperature', 21, '°C')]})):
            await self.run_device(item['id'], [reading(value=122)])
        state = self.manager.get(item['id'])
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['values']['water']['value'], before['value'])
        self.assertEqual(state['values']['water']['timestamp'], before['timestamp'])
        self.assertEqual(state['values']['temperature']['value'], 21)

    async def test_invalid_values_never_replace_good_reading(self):
        item = await configured_device(self.studio)
        await self.run_device(item['id'], [reading()])
        for bad in (True, None, '124', -1, float('inf'), float('nan')):
            await self.run_device(item['id'], [reading(value=bad)])
            self.assertEqual(self.manager.get(item['id'])['values']['water']['value'], 123)
            self.assertEqual(self.manager.get(item['id'])['status'], 'error')
        for readings in ([], [reading(), reading()], [reading(status='unreadable')]):
            await self.run_device(item['id'], readings)
            self.assertEqual(self.manager.get(item['id'])['values']['water']['value'], 123)

    async def test_save_applies_configuration_and_enabled_state_atomically(self):
        item = await configured_device(self.studio)
        await self.run_device(item['id'], [reading()])
        identity = item['identity']
        old_bindings = copy.deepcopy(self.manager.get(item['id'])['bindings'])
        config = copy.deepcopy(item['draft']);config['name'] = 'Saved name';config['interval_minutes'] = 15
        with patch.object(self.action, 'check_config', side_effect=Invalid('Action temporarily unavailable.')):
            result = await self.manager.save(item['id'], {'config': config, 'enabled': False})
        self.assertFalse(result['enabled']);self.assertEqual(result['status'], 'disabled')
        self.assertIsNone(result['next_run']);self.assertEqual(result['draft'], config);self.assertEqual(result['active'], config)
        self.assertEqual(result['identity'], identity);self.assertEqual(result['values']['water']['value'], 123)
        self.assertEqual(self.manager.get(item['id'])['bindings'], old_bindings)
        with self.assertRaises(Invalid):self.manager.start_read(item['id'])
        original = copy.deepcopy(self.manager.data)
        with patch.object(self.manager.store, 'write', side_effect=OSError('storage failed')):
            with self.assertRaises(OSError):await self.manager.save(item['id'], {'config': config, 'enabled': True})
        self.assertEqual(self.manager.data, original)
        with patch.object(self.action, 'check_config', side_effect=Invalid('Invalid Action settings.')):
            with self.assertRaises(Invalid):await self.manager.save(item['id'], {'config': config, 'enabled': True})
        self.assertEqual(self.manager.data, original)
        result = await self.manager.save(item['id'], {'config': config, 'enabled': True})
        self.assertTrue(result['enabled']);self.assertEqual(result['active'], config)
        self.assertAlmostEqual(result['next_run'] - time.time(), 900, delta=2)
        restored = Studio(self.tmp.name)
        try:
            self.assertTrue(restored.devices.public(item['id'])['enabled'])
            self.assertEqual(restored.devices.public(item['id'])['active'], config)
        finally:await restored.close()

    async def test_unpublished_inactive_settings_remain_editable_until_enabled(self):
        item = self.manager.create({'name': 'Inactive setup'})
        config = copy.deepcopy(item['draft']);config['sensors'] = [sensor(kind='number', unit='')]
        await self.manager.save(item['id'], {'config': config, 'enabled': False})
        self.assertEqual(self.manager.get(item['id'])['sensor_identities'], {})
        config['sensors'] = [sensor()]
        result = await self.manager.save(item['id'], {'config': config, 'enabled': False})
        self.assertEqual(result['active']['sensors'][0]['unit'], 'm³')
        self.assertFalse(result['enabled'])
        await self.manager.save(item['id'], {'config': config, 'enabled': True})
        self.assertIn('water', self.manager.get(item['id'])['sensor_identities'])

    async def test_disabled_save_preserves_identity_rules_and_validates_enabled(self):
        item = await configured_device(self.studio)
        config = copy.deepcopy(item['draft']);config['sensors'][0]['unit'] = 'L'
        with self.assertRaises(Invalid):await self.manager.save(item['id'], {'config': config, 'enabled': False})
        for enabled in (0, 1, None, 'true'):
            with self.assertRaises(Invalid):await self.manager.save(item['id'], {'config': item['draft'], 'enabled': enabled})
        with self.assertRaises(Invalid):await self.manager.save(item['id'], {'config': item['draft'], 'enabled': False, 'activate': True})
        empty = self.manager.create({'name': 'Empty device'})
        result = await self.manager.save(empty['id'], {'config': empty['draft'], 'enabled': False})
        self.assertFalse(result['enabled'])
        with self.assertRaises(Invalid):await self.manager.save(empty['id'], {'config': empty['draft'], 'enabled': True})

    async def test_draft_snapshot_and_removed_sensor_history(self):
        item = await configured_device(self.studio)
        await self.run_device(item['id'], [reading()])
        config = copy.deepcopy(item['draft']);config['name'] = 'New draft'
        await self.manager.save(item['id'], {'config': config, 'activate': False})
        self.assertEqual(self.manager.get(item['id'])['active']['name'], 'Water meter')
        config['sensors'] = [sensor('temperature', kind='temperature', unit='°C')]
        await self.manager.save(item['id'], {'config': config, 'activate': True})
        config['sensors'] = [sensor()]
        await self.manager.save(item['id'], {'config': config, 'activate': True})
        await self.run_device(item['id'], [reading(value=100)])
        self.assertEqual(self.manager.get(item['id'])['values']['water']['value'], 123)
        config['sensors'] = [sensor(kind='number', unit='')]
        with self.assertRaises(Invalid):await self.manager.save(item['id'], {'config': config, 'activate': True})

    async def test_activation_rebases_other_device_changes_and_blocks_same_device(self):
        first = await configured_device(self.studio)
        second = await configured_device(self.studio)
        original = self.action.check_config
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def check(config):
            entered.set();await proceed.wait();return await original(config)
        with patch.object(self.action, 'check_config', check):
            saving = asyncio.create_task(self.manager.save(second['id'], {'config': second['draft'], 'activate': True}))
            await entered.wait()
            self.manager.deactivate(first['id'])
            third = self.manager.create({'name': 'Concurrent'})
            with self.assertRaises(Invalid):self.manager.start_read(second['id'])
            proceed.set();await saving
        self.assertFalse(self.manager.get(first['id'])['enabled'])
        self.assertEqual(self.manager.get(third['id'])['draft']['name'], 'Concurrent')

    async def test_persist_intent_before_execution_and_failed_result_pauses(self):
        item = await configured_device(self.studio)
        with patch.object(self.manager.store, 'write', side_effect=OSError('secret')):
            with self.assertRaises(OSError):self.manager.start_read(item['id'])
        self.assertFalse(self.manager.busy(item['id']))
        original = self.manager.store.write
        def fail_result(name, value):
            if value['items'][item['id']]['values']:raise OSError('secret')
            return original(name, value)
        with patch.object(self.manager.store, 'write', fail_result):
            await self.run_device(item['id'], [reading()])
        self.assertIsNotNone(self.manager.error)
        with self.assertRaises(Invalid):self.manager.start_read(item['id'])
        self.assertNotIn('secret', json.dumps(self.manager.public(item['id'])))
        restored = Studio(self.tmp.name)
        self.assertEqual(restored.devices.get(item['id'])['status'], 'interrupted')
        self.assertGreater(restored.devices.get(item['id'])['next_run'], time.time())
        await restored.close()

    async def test_action_result_persistence_failure_never_publishes_measurement(self):
        item = await configured_device(self.studio)
        original = self.action.store.write
        def fail_result(name, value):
            if name == 'action-jobs.json' and any(v.get('result') for v in value.values()):raise OSError('disk')
            original(name, value)
        with patch.object(self.action.store, 'write', fail_result):await self.run_device(item['id'], [reading()])
        self.assertNotIn('value', self.manager.get(item['id'])['values']['water'])
        self.assertEqual(next(iter(self.action.jobs.values()))['execution'], 'processing')

    async def test_busy_serialization_restart_and_no_catch_up(self):
        first = await configured_device(self.studio)
        second = await configured_device(self.studio)
        entered, finish = asyncio.Event(), asyncio.Event()
        running = 0;peak = 0
        async def execute(*args):
            nonlocal running, peak
            running += 1;peak = max(peak, running);entered.set();await finish.wait();running -= 1
            return {'readings': [reading()]}
        with patch.object(self.action, 'execute', execute):
            self.manager.start_read(first['id']);await entered.wait();self.manager.start_read(second['id'])
            with self.assertRaises(Invalid):self.manager.start_read(first['id'])
            with self.assertRaises(Invalid):self.manager.deactivate(first['id'])
            with self.assertRaises(Invalid):await self.studio.delete({'id':'reader','confirmed':True})
            finish.set();await asyncio.gather(*self.manager.tasks.values())
        self.assertEqual(peak, 1)
        self.assertGreater(self.manager.get(first['id'])['next_run'], time.time())
        restored = Studio(self.tmp.name)
        self.assertEqual(restored.devices.public(first['id'])['identity'], first['identity'])
        self.assertEqual(restored.devices.get(first['id'])['values']['water']['value'], 123)
        await restored.close()

    async def test_discovery_opt_in_draft_and_only_devices(self):
        self.assertFalse(any(t.endswith('/config') for t in self.studio.discovery.desired()))
        item = await configured_device(self.studio)
        self.assertFalse(any(t.endswith('/config') for t in self.studio.discovery.desired()))
        broker_id = (await self.studio.brokers.save(broker()))['brokers'][0]['id']
        await self.studio.discovery.save({**DEFAULT, 'enabled': True, 'broker_id': broker_id})
        config = copy.deepcopy(item['draft']);config['publish'] = True
        await self.manager.save(item['id'], {'config': config, 'activate': False})
        self.assertFalse(any(t.endswith('/config') for t in self.studio.discovery.desired()))
        await self.manager.save(item['id'], {'config': config, 'activate': True})
        desired = self.studio.discovery.desired()
        entities = [p for t, p in desired.items() if t.endswith('/config')]
        self.assertEqual(len(entities), 5)
        self.assertEqual({tuple(e['device']['identifiers']) for e in entities}, {(item['identity'],)})
        self.manager.deactivate(item['id'])
        self.assertEqual(self.studio.discovery.desired()[item['identity']+'/status'], 'disabled')
        before = copy.deepcopy(self.manager.data)
        with patch('app.discovery.MQTT') as transport:
            transport.return_value.publish.side_effect = OSError('offline')
            with self.assertRaises(OSError):await self.studio.discovery.reconcile()
        self.assertEqual(self.manager.data, before)

    async def test_disabled_save_reconciles_only_removed_owned_discovery_topics(self):
        broker_id = (await self.studio.brokers.save(broker()))['brokers'][0]['id']
        await self.studio.discovery.save({**DEFAULT, 'enabled': True, 'broker_id': broker_id})
        item = await configured_device(self.studio, [sensor(), sensor('temperature', kind='temperature', unit='°C')], publish=True)
        await self.run_device(item['id'], [reading(), reading('temperature', 20, '°C')])
        config = copy.deepcopy(item['draft'])
        with patch('app.discovery.MQTT') as transport, patch.object(self.action, 'execute', AsyncMock()) as execute:
            await self.studio.discovery.reconcile()
            before = self.studio.discovery.desired()
            transport.return_value.publish.reset_mock()
            config['sensors'] = config['sensors'][:1]
            await self.manager.save(item['id'], {'config': config, 'enabled': False})
            desired = self.studio.discovery.desired()
            await self.studio.discovery.reconcile()
            removed = {call.args[0] for call in transport.return_value.publish.call_args_list if call.args[1] == ''}
            self.assertEqual(removed, set(before) - set(desired))
            self.assertEqual(desired[item['identity'] + '/status'], 'disabled')
            self.assertEqual(self.manager.public(item['id'])['values']['temperature']['value'], 20)
            for topic in set(before) & set(desired):
                if topic.endswith('/config'):self.assertEqual(before[topic]['unique_id'], desired[topic]['unique_id'])
            transport.return_value.publish.reset_mock()
            config['publish'] = False
            await self.manager.save(item['id'], {'config': config, 'enabled': False})
            await self.studio.discovery.reconcile()
            removed = {call.args[0] for call in transport.return_value.publish.call_args_list if call.args[1] == ''}
            self.assertEqual(removed, set(desired) - set(self.studio.discovery.desired()))
            execute.assert_not_awaited()

    async def test_overdue_schedule_runs_once_and_disabled_stays_idle(self):
        item = await configured_device(self.studio)
        candidate = copy.deepcopy(self.manager.data)
        candidate['items'][item['id']]['next_run'] = time.time() - 86400
        self.manager.commit(candidate)
        with patch.object(self.action, 'execute', AsyncMock(return_value={'readings': [reading()]})) as execute:
            self.manager.task = asyncio.create_task(self.manager.run())
            for _ in range(100):
                if self.manager.get(item['id'])['values']:break
                await asyncio.sleep(.01)
            self.assertTrue(self.manager.get(item['id'])['values'])
            await self.manager.tasks[item['id']]
            self.manager.deactivate(item['id'])
            await asyncio.sleep(.55)
            execute.assert_awaited_once()
        self.assertIsNone(self.manager.get(item['id'])['next_run'])

    async def test_max_change_and_reserved_identity_units(self):
        item = await configured_device(self.studio, [{**sensor(), 'max_change': 5}])
        await self.run_device(item['id'], [reading(value=100)])
        await self.run_device(item['id'], [reading(value=106)])
        self.assertEqual(self.manager.get(item['id'])['values']['water']['value'], 100)
        await self.run_device(item['id'], [reading(value=104)])
        self.assertEqual(self.manager.get(item['id'])['values']['water']['value'], 104)
        config = copy.deepcopy(item['draft']);config['sensors'].append(sensor())
        with self.assertRaises(Invalid):await self.manager.save(item['id'], {'config':config,'activate':True})

    async def test_action_settings_are_snapshotted_and_examples_never_executed(self):
        with patch.object(self.action.plugin, 'validate', return_value={}):
            self.action.config['draft']['settings'] = {'source': 'original'}
            item = await configured_device(self.studio)
            self.action.config['draft']['settings'] = {'source': 'changed'}
            with patch.dict(self.action.plugin.metadata, {'example': {'request_id':'sample','data': {'private':'example-only'}}}):
                execute = await self.run_device(item['id'], [reading()])
            execute.assert_awaited_once_with({}, {'source': 'original'})
        with self.assertRaises(Invalid):await self.studio.delete({'id':'reader','confirmed':True})

    async def test_generic_reading_uses_free_unit_without_domain_rules(self):
        item = await configured_device(self.studio, [sensor('sample', kind='number', unit='widgets')])
        await self.run_device(item['id'], [reading('sample', -200, 'widgets')])
        self.assertEqual(self.manager.get(item['id'])['values']['sample']['value'], -200)
        self.assertEqual(self.manager.get(item['id'])['status'], 'ready')

    async def test_profile_migration_preserves_ids_values_and_ha_metadata(self):
        item = await configured_device(self.studio)
        await self.run_device(item['id'], [reading()])
        original = copy.deepcopy(self.manager.get(item['id']))
        legacy = copy.deepcopy(self.manager.data)
        saved = legacy['items'][item['id']]
        for config in (saved['draft'], saved['active']):
            config['type'] = 'water_meter'
            for definition in config['sensors']:
                definition.pop('device_class');definition.pop('cumulative');definition['kind'] = 'water'
        saved['sensor_identities']['water'] = ['water', 'm³']
        self.manager.store.write('devices.json', legacy)
        restored = Studio(self.tmp.name)
        migrated = restored.devices.get(item['id'])
        self.assertEqual(migrated, original)
        self.assertNotIn('kind', migrated['draft']['sensors'][0])
        self.assertEqual(migrated['active']['sensors'][0]['device_class'], 'water')
        self.assertTrue(migrated['active']['sensors'][0]['cumulative'])
        await restored.devices.save(item['id'], {'config':migrated['draft'], 'activate':True})
        await restored.close()

    async def test_text_readings_preserve_serial_numbers_and_exclude_numeric_previous(self):
        item = await configured_device(self.studio, [sensor(), sensor('serial', kind='number', unit='')])
        with patch.dict(self.action.plugin.metadata, {'input_schema': {'properties': {'data': {'properties': {'previous': {}}}}}}):
            await self.run_device(item['id'], [reading(), reading('serial', '000123-AB', '')])
            execute = await self.run_device(item['id'], [reading(value=124), reading('serial', '000123-AB', '')])
            previous = execute.call_args.args[0]['previous']
            self.assertEqual(set(previous), {'water'})
            self.assertEqual(previous['water']['value'], 123)
        state = self.manager.get(item['id'])
        self.assertEqual(state['status'], 'ready')
        self.assertEqual(state['values']['serial']['value'], '000123-AB')
        restored = Studio(self.tmp.name)
        self.assertEqual(restored.devices.get(item['id'])['values']['serial']['value'], '000123-AB')
        await restored.close()

    async def test_text_discovery_no_statistics_or_numeric_metadata(self):
        broker_id = (await self.studio.brokers.save(broker()))['brokers'][0]['id']
        await self.studio.discovery.save({**DEFAULT, 'enabled': True, 'broker_id': broker_id})
        item = await configured_device(self.studio, [sensor(), sensor('serial', kind='number', unit='')], publish=True)
        await self.run_device(item['id'], [reading(), reading('serial', '00000123', '')])
        desired = self.studio.discovery.desired();prefix = item['identity']
        serial = desired[f'homeassistant/sensor/{prefix}/value_serial/config']
        for key in ('state_class', 'unit_of_measurement', 'device_class'):
            self.assertNotIn(key, serial)
        self.assertEqual(desired[prefix+'/value_serial'], '00000123')
        numeric = desired[f'homeassistant/sensor/{prefix}/value_water/config']
        self.assertEqual(numeric['state_class'], 'total_increasing')

    async def test_invalid_text_and_type_changes_preserve_prior_value(self):
        item = await configured_device(self.studio, [sensor('serial', kind='number', unit='')])
        await self.run_device(item['id'], [reading('serial', '000123', '')])
        for invalid in ('', '  ', 'A'*256, None, True, [], {}, 123):
            await self.run_device(item['id'], [reading('serial', invalid, '')])
            state = self.manager.get(item['id'])
            self.assertEqual(state['status'], 'error')
            self.assertEqual(state['values']['serial']['value'], '000123')
        number = await configured_device(self.studio, [sensor('sample', kind='number', unit='')])
        await self.run_device(number['id'], [reading('sample', 123, '')])
        await self.run_device(number['id'], [reading('sample', '000123', '')])
        self.assertEqual(self.manager.get(number['id'])['values']['sample']['value'], 123)

    async def test_text_rejects_numeric_options_with_clear_errors(self):
        for patch_config in ({'cumulative': True}, {'max_change': 1}, {'device_class': 'water'}, {'unit': 'm³'}):
            definition = {**sensor('serial', kind='number', unit=''), **patch_config}
            item = await configured_device(self.studio, [definition])
            await self.run_device(item['id'], [reading('serial', '000123', definition['unit'])])
            value = self.manager.get(item['id'])['values']['serial']
            self.assertNotIn('value', value)
            self.assertIn('Text readings require', value['error'])

    async def test_device_owns_unit_without_action_unit_or_conversion(self):
        item = await configured_device(self.studio)
        for output in ({'id': 'water', 'value': 123, 'status': 'read'}, reading(unit=''), reading(unit='L')):
            await self.run_device(item['id'], [output])
            state = self.manager.get(item['id'])
            self.assertEqual(state['status'], 'ready')
            self.assertEqual(state['values']['water']['value'], 123)
            self.assertEqual(state['active']['sensors'][0]['unit'], 'm³')
        await self.run_device(item['id'], [{'id': 'water', 'value': 122, 'status': 'read'}])
        state = self.manager.get(item['id'])
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['values']['water']['value'], 123)

    def rate_sensor(self, **rate):
        return {**sensor('flow', action='', kind='number', unit='L/min'), 'reading': '',
                'rate': {'source': 'water', 'factor': 1000, 'time_base': 'minute', **rate}}

    async def rate_read(self, id, value, measured_at):
        return await self.run_device(id, [{**reading(value=value), 'measured_at': measured_at}])

    async def test_rate_average_zero_restart_and_single_action_execution(self):
        item = await configured_device(self.studio, [self.rate_sensor(), sensor()])
        execute = await self.rate_read(item['id'], 123.450, 1000)
        execute.assert_awaited_once()
        self.assertEqual(self.manager.get(item['id'])['values']['flow']['status'], 'unavailable')
        self.assertNotIn('value', self.manager.get(item['id'])['values']['flow'])
        await self.rate_read(item['id'], 123.465, 1300)
        self.assertAlmostEqual(self.manager.get(item['id'])['values']['flow']['value'], 3)
        restored = Studio(self.tmp.name)
        try:
            with patch.object(restored.get('reader'), 'execute', AsyncMock(return_value={'readings': [{**reading(value=123.465), 'measured_at': 1600}]})) as execute:
                restored.devices.start_read(item['id'])
                await restored.devices.tasks[item['id']]
                execute.assert_awaited_once()
            result = restored.devices.get(item['id'])
            self.assertEqual(result['values']['flow']['value'], 0)
            self.assertEqual(result['status'], 'ready')
        finally:await restored.close()

    async def test_rate_rejects_invalid_source_and_time_preserves_last_valid_baseline(self):
        item = await configured_device(self.studio, [sensor(), self.rate_sensor()])
        await self.rate_read(item['id'], 100, 1000)
        await self.rate_read(item['id'], 101, 1060)
        for value, at in ((102,1060), (102,900), (99,1120), (True,1120), ('wrong',1120), (None,1120), (float('inf'),1120), (102,True), (102,-1), (102,time.time()+3600)):
            await self.rate_read(item['id'], value, at)
            state = self.manager.get(item['id'])['values']
            self.assertEqual(state['flow']['status'], 'unavailable')
            self.assertAlmostEqual(state['flow']['value'], 1000)
            self.assertEqual(state['water']['measured_at'], 1060)
        await self.rate_read(item['id'], 103, 1180)
        self.assertAlmostEqual(self.manager.get(item['id'])['values']['flow']['value'], 1000)
        with patch.object(self.action, 'execute', AsyncMock(side_effect=RuntimeError('failed'))):
            self.manager.start_read(item['id']);await self.manager.tasks[item['id']]
        self.assertEqual(self.manager.get(item['id'])['values']['flow']['status'], 'unavailable')

    async def test_rate_validation_rejects_cycles_bad_factors_and_missing_sources(self):
        item = self.manager.create({'name':'Rates'})
        for patch_rate in ({'source':'missing'}, {'source':'flow'}, {'factor':True}, {'factor':0}, {'factor':-1}, {'factor':float('inf')}, {'factor':None}, {'time_base':'day'}, {'time_base':[]}):
            with self.subTest(patch_rate=patch_rate), self.assertRaises(Invalid):
                self.manager.validate({**item['draft'], 'sensors':[sensor(), self.rate_sensor(**patch_rate)]})
        derived = self.rate_sensor();other = {**self.rate_sensor(source='flow'), 'id':'other'}
        with self.assertRaises(Invalid):self.manager.validate({**item['draft'], 'sensors':[sensor(), derived, other]})
        for patch_sensor in ({'cumulative':True}, {'action_id':'reader'}, {'reading':'water'}, {'rate':None}):
            with self.assertRaises(Invalid):self.manager.validate({**item['draft'], 'sensors':[sensor(), {**derived, **patch_sensor}]})

    async def test_rate_time_bases_signed_values_and_nonfinite_result(self):
        for base, expected in (('second', -1), ('minute', -60), ('hour', -3600)):
            item = await configured_device(self.studio, [sensor(kind='number'), self.rate_sensor(time_base=base, factor=1)])
            await self.rate_read(item['id'], 200, 1000)
            await self.rate_read(item['id'], 140, 1060)
            self.assertEqual(self.manager.get(item['id'])['values']['flow']['value'], expected)
        item = await configured_device(self.studio, [sensor(), self.rate_sensor(factor=1e308)])
        await self.rate_read(item['id'], 0, 1000)
        await self.rate_read(item['id'], 1e308, 1060)
        self.assertEqual(self.manager.get(item['id'])['values']['flow']['status'], 'unavailable')

    async def test_rate_discovery_availability_and_disabled_state(self):
        broker_id = (await self.studio.brokers.save(broker()))['brokers'][0]['id']
        await self.studio.discovery.save({**DEFAULT, 'enabled':True, 'broker_id':broker_id})
        item = await configured_device(self.studio, [sensor(), self.rate_sensor()], publish=True)
        prefix = item['identity'];topic = prefix+'/available_flow'
        await self.rate_read(item['id'], 100, 1000)
        desired = self.studio.discovery.desired()
        self.assertEqual(desired[topic], 'offline')
        entity = desired[f'homeassistant/sensor/{prefix}/value_flow/config']
        self.assertEqual(entity['state_class'], 'measurement')
        self.assertEqual(entity['availability_mode'], 'all')
        self.assertNotIn('availability_topic', entity)
        self.assertIn({'topic':topic}, entity['availability'])
        await self.rate_read(item['id'], 101, 1060)
        self.assertEqual(self.studio.discovery.desired()[topic], 'online')
        await self.rate_read(item['id'], 99, 1120)
        self.assertEqual(self.studio.discovery.desired()[topic], 'offline')
        await self.rate_read(item['id'], 102, 1180)
        self.manager.deactivate(item['id'])
        self.assertEqual(self.studio.discovery.desired()[topic], 'offline')

    async def test_rate_source_rebinding_resets_baseline_and_formula_identity_is_stable(self):
        item = await configured_device(self.studio, [sensor(), self.rate_sensor()])
        await self.rate_read(item['id'], 100, 1000)
        await self.rate_read(item['id'], 101, 1060)
        config = copy.deepcopy(item['draft']);config['sensors'][0]['reading'] = 'replacement'
        await self.manager.save(item['id'], {'config':config, 'enabled':True})
        self.assertNotIn('water', self.manager.get(item['id'])['values'])
        self.assertNotIn('flow', self.manager.get(item['id'])['values'])
        config['sensors'][1]['rate']['factor'] = 1
        with self.assertRaises(Invalid):await self.manager.save(item['id'], {'config':config, 'enabled':True})

    async def test_rate_integer_overflow_does_not_pause_other_devices(self):
        item = await configured_device(self.studio, [sensor(kind='number'), self.rate_sensor()])
        await self.rate_read(item['id'], -10**308, 1000)
        await self.rate_read(item['id'], 10**308, 1060)
        self.assertIsNone(self.manager.error)
        self.assertEqual(self.manager.get(item['id'])['values']['flow']['status'], 'unavailable')
        self.assertEqual(self.manager.get(item['id'])['status'], 'partial')

    async def test_rate_interrupted_restart_keeps_durable_baseline_without_replay(self):
        item = await configured_device(self.studio, [sensor(), self.rate_sensor()])
        await self.rate_read(item['id'], 100, 1000)
        await self.rate_read(item['id'], 101, 1060)
        entered = asyncio.Event()
        async def blocked(*args):
            entered.set();await asyncio.Event().wait()
        with patch.object(self.action, 'execute', blocked):
            self.manager.start_read(item['id']);await entered.wait()
            self.manager.tasks[item['id']].cancel()
            with self.assertRaises(asyncio.CancelledError):await self.manager.tasks[item['id']]
        restored = Studio(self.tmp.name)
        try:
            self.assertEqual(restored.devices.get(item['id'])['status'], 'interrupted')
            self.assertEqual(restored.devices.get(item['id'])['values']['flow']['status'], 'unavailable')
            self.assertEqual(restored.devices.tasks, {})
            with patch.object(restored.get('reader'), 'execute', AsyncMock(return_value={'readings':[{**reading(value=103), 'measured_at':1180}]})) as execute:
                restored.devices.start_read(item['id']);await restored.devices.tasks[item['id']]
                execute.assert_awaited_once()
            self.assertAlmostEqual(restored.devices.get(item['id'])['values']['flow']['value'], 1000)
        finally:await restored.close()

    async def test_rate_maximum_change_accepts_source_but_preserves_rate(self):
        item = await configured_device(self.studio, [sensor(), {**self.rate_sensor(), 'max_change':500}])
        for value, at in ((100,1000), (101,1060), (104,1120)):
            await self.rate_read(item['id'], value, at)
        state = self.manager.get(item['id'])['values']
        self.assertAlmostEqual(state['flow']['value'], 1000)
        self.assertEqual(state['flow']['status'], 'unavailable')
        self.assertEqual(state['water']['value'], 104)
        await self.rate_read(item['id'], 105,1180)
        self.assertEqual(self.manager.get(item['id'])['values']['flow']['status'], 'ready')
