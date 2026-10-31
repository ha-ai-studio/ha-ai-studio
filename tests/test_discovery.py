import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from app.actions import Studio
from app.discovery import DEFAULT
from app.domain import Invalid
from test_actions import broker
from test_devices import configured_device, sensor


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Historical instances: temporarily expose the old type only in this fixture.
        from app.action_types import TYPES
        legacy_catalog=patch("app.actions.CATALOG",[TYPES["echo"].metadata]);legacy_catalog.start();self.addCleanup(legacy_catalog.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = Studio(self.tmp.name)
        self.broker = (await self.studio.brokers.save(broker()))['brokers'][0]['id']
        self.d = self.studio.discovery
        self.config = {**DEFAULT, 'enabled': True, 'broker_id': self.broker}
        await self.studio.create({'id':'reader','name':'Reader','type':'echo'})
        await self.d.save(self.config)
        self.device = await configured_device(self.studio, publish=True)
        self.prefix = self.device['identity']

    async def asyncTearDown(self):
        await self.studio.close()
        self.tmp.cleanup()

    async def test_only_explicit_devices_and_stable_ids(self):
        desired=self.d.desired()
        entities=[p for t,p in desired.items() if t.endswith('/config')]
        self.assertEqual(len(entities),4)
        self.assertTrue(all(p['device']['identifiers']==[self.prefix] for p in entities))
        self.assertFalse(any('active_actions' in t for t in desired))
        self.assertFalse(any(self.studio.get('reader').identity['prefix'] in t for t in desired))
        config=copy.deepcopy(self.device['draft']);config['name']='Renamed'
        await self.studio.devices.save(self.device['id'],{'config':config,'activate':False})
        self.assertEqual(self.d.desired(),desired)
        await self.studio.devices.save(self.device['id'],{'config':config,'activate':True})
        self.assertEqual(set(self.d.desired()),set(desired))
        restored=Studio(self.tmp.name)
        self.assertEqual(restored.discovery.desired(),self.d.desired())
        self.assertEqual(restored.get('reader').identity,self.studio.get('reader').identity)
        await restored.close()
        with tempfile.TemporaryDirectory() as other_root:
            other=Studio(other_root)
            self.assertTrue(set(self.d.desired()).isdisjoint(other.discovery.desired()))
            await other.close()

    async def test_no_devices_no_entities(self):
        self.studio.devices.delete({'id':self.device['id'],'confirmed':True})
        self.assertFalse(any(t.endswith('/config') for t in self.d.desired()))
        await self.d.save(dict(DEFAULT))
        with patch('app.discovery.MQTT') as mqtt:
            await self.d.reconcile();mqtt.assert_not_called()
        with self.assertRaises(Invalid):await self.d.save({**DEFAULT,'enabled':True})

    async def test_ownership_before_publish_and_cleanup(self):
        await self.d.save(self.config)
        calls=[]
        def publish(topic,payload,**kwargs):
            owned=self.studio.store.read('discovery.json')['owned']
            self.assertTrue(any(topic in i['topics'] for i in owned))
            calls.append((topic,payload,kwargs))
        with patch('app.discovery.MQTT') as transport:
            transport.return_value.publish.side_effect=publish
            await self.d.reconcile()
            self.assertTrue(calls)
            self.assertTrue(all(k['retain'] for _,_,k in calls))
            await self.d.save({**DEFAULT})
            await self.d.reconcile()
        self.assertEqual(self.d.data['owned'],[])
        self.assertTrue(any(p=='' for _,p,_ in calls))

    async def test_pending_cleanup_survives_restart_and_guards_broker(self):
        await self.d.save(self.config)
        with patch('app.discovery.MQTT'):await self.d.reconcile()
        await self.d.save(dict(DEFAULT))
        with patch('app.discovery.MQTT') as transport:
            transport.return_value.connect.side_effect=OSError('SECRET')
            with self.assertRaises(OSError):await self.d.reconcile()
        restored=Studio(self.tmp.name)
        self.assertGreater(restored.discovery.public()['pending_cleanup'],0)
        with self.assertRaises(Invalid):await restored.brokers.delete({'id':self.broker,'confirmed':True})
        edited={**broker('different'),'id':self.broker}
        with self.assertRaises(Invalid):await restored.brokers.save(edited)
        self.assertNotIn('SECRET',json.dumps(restored.discovery.public()))
        with patch('app.discovery.MQTT'):await restored.discovery.reconcile()
        await restored.brokers.delete({'id':self.broker,'confirmed':True})

    async def test_delete_only_owned_topics_and_switch(self):
        expected=set(self.d.desired())-{self.studio.namespace+'/discovery/availability'}
        with patch('app.discovery.MQTT') as transport:
            await self.d.reconcile()
            self.studio.devices.delete({'id':self.device['id'],'confirmed':True})
            await self.d.reconcile()
            deleted=[c.args[0] for c in transport.return_value.publish.call_args_list if c.args[1]=='']
            self.assertEqual(set(deleted),expected)
            before=set(self.studio.brokers.all());await self.studio.brokers.save(broker('second'));other=(set(self.studio.brokers.all())-before).pop()
            await self.d.save({**self.config,'broker_id':other});await self.d.reconcile()
            self.assertEqual({i['broker_id'] for i in self.d.data['owned']},{other})

    async def test_prefix_validation_and_legacy_identity_preserved(self):
        for prefix in ('','homeassistant/#','$SYS','bad\n'):
            with self.assertRaises(Invalid):await self.d.save({**self.config,'prefix':prefix})
        service=self.studio.get('reader');identity=copy.deepcopy(service.identity)
        restored=Studio(self.tmp.name)
        self.assertEqual(restored.get('reader').identity,identity)
        self.assertFalse(any(identity['prefix'] in t for t in restored.discovery.desired()))
        await restored.close()

    async def test_persistence_failure_never_authorizes_publication(self):
        before=copy.deepcopy(self.d.data)
        with patch.object(self.d.store,'write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):await self.d.save({**self.config,'prefix':'changed'})
        self.assertEqual(self.d.data,before)
        await self.d.save(self.config)
        original=self.d.store.write
        def fail_manifest(name,value):
            if name=='discovery.json':raise OSError('disk full')
            original(name,value)
        with patch('app.discovery.MQTT') as transport:
            with patch.object(self.d.store,'write',side_effect=fail_manifest):
                with self.assertRaises(OSError):await self.d.reconcile()
            transport.return_value.publish.assert_not_called()
            self.assertEqual(self.d.data['owned'],[])
            await self.d.reconcile()
            self.assertTrue(self.d.store.read('discovery.json')['owned'])

    async def test_discovery_route_collisions(self):
        service=self.studio.get('reader')
        config=copy.deepcopy(service.config['draft'])
        config['input']['broker_id']=self.broker
        config['output'].update(broker_id=self.broker,topic=self.prefix+'/value_water')
        with self.assertRaises(Invalid):await service.save(config,True)
        with self.assertRaises(Invalid):await self.d.save({**self.config,'ha_status_topic':self.studio.namespace+'/discovery/availability'})
        config['output']['topic']='safe/result';await service.save(config,True)
        self.assertTrue(service.enabled)

    async def test_cleanup_connection_survives_missing_supervisor(self):
        await self.d.save(self.config)
        with patch('app.discovery.MQTT'):await self.d.reconcile()
        # Simulate a disappeared discovered registry item after a restart.
        self.studio.brokers.manual.pop(self.broker)
        await self.d.save({**self.config,'enabled':False})
        with patch('app.discovery.MQTT') as transport:
            await self.d.reconcile()
            self.assertEqual(transport.return_value.connect.call_args.args[0]['mqtt']['host'],'broker')
        self.assertEqual(self.d.data['owned'],[])
        self.assertEqual(self.studio.brokers.cleanup_connections,{})

    async def test_legacy_action_and_studio_cleanup_failure_restart(self):
        legacy=['homeassistant/sensor/old/status/config','old/status',
                'homeassistant/sensor/root/active_actions/config','root/discovery/active_actions']
        endpoint=self.d.fingerprint(self.broker)
        self.studio.brokers.pin_cleanup(self.broker,endpoint)
        self.d.data['owned']=[{'broker_id':self.broker,'endpoint':endpoint,'topics':legacy.copy()}]
        self.d.persist()
        calls=[]
        def fail_second(topic,payload,**kwargs):
            calls.append((topic,payload))
            if len(calls)==2:raise OSError('interrupted cleanup')
        with patch('app.discovery.MQTT') as transport:
            transport.return_value.publish.side_effect=fail_second
            with self.assertRaises(OSError):await self.d.reconcile()
        self.assertTrue(all(payload=='' for _,payload in calls))
        restored=Studio(self.tmp.name)
        self.assertEqual(restored.discovery.public()['pending_cleanup'],3)
        with patch('app.discovery.MQTT') as transport:
            await restored.discovery.save({**self.config,'prefix':'custom-ha'})
            await restored.discovery.reconcile()
            calls=[(c.args[0],c.args[1]) for c in transport.return_value.publish.call_args_list]
            self.assertTrue(all(payload=='' for _,payload in calls[:3]))
            self.assertEqual({t for t,p in calls if p==''},set(legacy)-{legacy[0]})
            configs=[topic for item in restored.discovery.data['owned'] for topic in item['topics'] if topic.endswith('/config')]
            self.assertEqual(len(configs),4)
            self.assertTrue(all(t.startswith('custom-ha/') for t in configs))
        await restored.close()

    async def test_saved_retry_route_blocks_discovery_collision(self):
        await self.studio.create({'id':'echo','name':'Echo','type':'echo'})
        service=self.studio.get('echo');config=copy.deepcopy(service.config['draft'])
        config['input']['broker_id']=self.broker
        config['output'].update(broker_id=self.broker,topic=self.prefix+'/status')
        service.jobs={'old':{'config':config}}
        with self.assertRaises(Invalid):await self.d.save(self.config)
