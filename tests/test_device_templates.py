"""Portable device configuration, explicit bindings and atomic inactive imports."""
import copy
import unittest
from unittest.mock import patch
import test_devices
from test_devices import sensor
from app.devices import Devices
from app.domain import Invalid


class DeviceTemplateTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_devices.DeviceTests.asyncSetUp
    asyncTearDown = test_devices.DeviceTests.asyncTearDown
    async def template(self):
        item = self.manager.create({'name': 'Meter'})
        config = item['draft']
        config['sensors'] = [sensor(), {**sensor('flow'), 'action_id': '', 'reading': '', 'cumulative': False,
            'rate': {'source': 'water', 'factor': 1000, 'time_base': 'minute'}}]
        await self.manager.save(item['id'], {'config': config, 'activate': False})
        return item, self.manager.export_template(item['id'])

    async def test_template_roundtrip_is_portable_and_inactive(self):
        original, template = await self.template()
        stored = self.manager.get(original['id'])
        stored.update(values={'water': {'value': 'private'}}, bindings={'secret': 'private'}, enabled=True)
        stored['draft']['publish'] = True
        template = self.manager.export_template(original['id'])
        self.assertEqual(set(template), {'format', 'version', 'config'})
        self.assertFalse(template['config']['publish'])
        self.assertEqual(template['config']['sensors'][0]['action_id'], 'action-1')
        self.assertTrue(stored['draft']['publish'])
        result = self.manager.import_template({'template': template, 'name': 'Imported', 'actions': {'action-1': 'other'}})
        self.assertNotEqual(result['identity'], original['identity'])
        self.assertFalse(result['enabled']);self.assertIsNone(result['active'])
        self.assertEqual(result['values'], {});self.assertIsNone(result['next_run'])
        self.assertEqual(result['draft']['sensors'][0]['action_id'], 'other')
        self.assertEqual(result['draft']['sensors'][1]['rate']['source'], 'water')
        self.assertEqual(Devices(self.studio).get(result['id'])['draft'], result['draft'])
        duplicate = self.manager.import_template({'template': template, 'name': 'Imported', 'actions': {'action-1': 'other'}})
        self.assertNotEqual(result['identity'], duplicate['identity'])

    async def test_invalid_templates_and_assignments_leave_no_partial_device(self):
        _, template = await self.template()
        invalid = [None, [], True, 'text', 1, {}, {**template, 'version': True}, {**template, 'version': 2},
                   {**template, 'extra': 'secret'}, {**template, 'config': None}]
        for edit in ({'publish': True}, {'interval_minutes': False}, {'sensors': [None]},
                     {'functions': [{'id': 'run', 'name': 'Run', 'action_id': [], 'function': 'execute'}]}):
            invalid.append({**template, 'config': {**template['config'], **edit}})
        duplicate = copy.deepcopy(template)
        duplicate['config']['sensors'].append(duplicate['config']['sensors'][0])
        invalid.append(duplicate)
        before = copy.deepcopy(self.manager.data)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(Invalid):
                self.manager.import_template({'template': value, 'name': 'Imported', 'actions': {'action-1': 'reader'}})
            self.assertEqual(before, self.manager.data)
        for bindings in ({}, {'action-1': 'missing'}, {'action-1': []}, {'action-1': 'reader', 'extra': 'other'}):
            with self.assertRaises(Invalid):
                self.manager.import_template({'template': template, 'name': 'Imported', 'actions': bindings})
            self.assertEqual(before, self.manager.data)
        with patch.object(self.manager.store, 'write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.manager.import_template({'template': template, 'name': 'Imported', 'actions': {'action-1': 'reader'}})
        self.assertEqual(before, self.manager.data)

    async def test_functions_share_slots_and_require_execute_capability(self):
        _, template = await self.template()
        template['config']['functions'] = [{'id': 'read', 'name': 'Read', 'action_id': 'action-1', 'function': 'execute'}]
        request = {'template': template, 'name': 'Functions', 'actions': {'action-1': 'other'}}
        before = copy.deepcopy(self.manager.data)
        with patch.dict(self.studio.get('other').plugin.metadata, {'functions': []}):
            with self.assertRaises(Invalid):self.manager.import_template(request)
        self.assertEqual(before, self.manager.data)
        with patch.dict(self.studio.get('other').plugin.metadata, {'functions': ['execute']}):
            result = self.manager.import_template(request)
        self.assertEqual(result['draft']['functions'][0]['action_id'], 'other')
        exported = self.manager.export_template(result['id'])
        self.assertEqual(exported['config']['functions'][0]['action_id'], exported['config']['sensors'][0]['action_id'])

    async def test_id_collision_never_overwrites_existing_or_deleted_devices(self):
        from types import SimpleNamespace
        original, template = await self.template()
        first = original['id'].removeprefix('device-')
        with patch('app.devices.uuid.uuid4', side_effect=[SimpleNamespace(hex=first), SimpleNamespace(hex='f' * 32)]):
            result = self.manager.import_template({'template': template, 'name': 'Other', 'actions': {'action-1': 'reader'}})
        self.assertEqual(result['id'], 'device-' + 'f' * 16)
        self.assertEqual(self.manager.get(original['id'])['draft']['name'], 'Meter')
        self.manager.delete({'id': original['id'], 'confirmed': True})
        with patch('app.devices.uuid.uuid4', side_effect=[SimpleNamespace(hex=first), SimpleNamespace(hex='e' * 32)]):
            result = self.manager.import_template({'template': template, 'name': 'Another', 'actions': {'action-1': 'reader'}})
        self.assertEqual(result['id'], 'device-' + 'e' * 16)
