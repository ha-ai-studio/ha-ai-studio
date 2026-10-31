"""Private image capability and preview contract; synthetic files, no MQTT."""
import asyncio
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from aiohttp.test_utils import TestClient,TestServer
from app.actions import Studio
from app.server import create_app
from app.plugins import manifest
from app.assets import image_info,preview
from app.domain import Invalid

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+afoUAAAAASUVORK5CYII=')

class AssetsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.app=create_app(self.root,dev=True);self.app.cleanup_ctx.clear()
        self.studio=next(v for v in self.app.values() if isinstance(v,Studio))
        source=self.studio.repositories.store.root/'sources'/'assets-test'/'actions'/'sample';source.mkdir(parents=True)
        data={'id':'sample','name':'Sample','description':'Synthetic assets test','version':'1','python':f'{sys.version_info.major}.{sys.version_info.minor}',
              'entrypoint':'action.py','capabilities':['assets','preview'],'settings_schema':{'type':'object'},'input_schema':{},'output_schema':{}}
        (source/'action.json').write_text(json.dumps(data));(source/'requirements.lock').write_text('')
        # Required keyword context exercises the capability's installation and run protocol.
        (source/'action.py').write_text('from pathlib import Path\nimport base64\n'
            'def execute(data,settings,*,context):\n return {"mode":context["mode"]}\n'
            'def preview(data,settings,*,context):\n'
            f' Path(context["output_dir"],"result.png").write_bytes(base64.b64decode({base64.b64encode(PNG).decode()!r}))\n'
            ' return {"mode":context["mode"]}\n')
        record={'type':'git-assets-sample','repository_id':'assets','commit':'a'*40,'snapshot':'assets-test','directory':'actions/sample','manifest':await manifest(source)}
        self.studio.repositories.state={'repositories':[{'id':'assets','url':'https://example.invalid','ref':'main','status':'Ready','actions':[record],'logs':[]}]}
        for name in ('first','second'):
            await self.studio.create({'id':name,'name':name,'type':record['type']});await self.studio.get(name).environment.task
            self.assertTrue(self.studio.get(name).environment.ready)
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close();await self.studio.close();self.tmp.cleanup()
    async def post(self,name,body,ident='first'):
        return await self.client.post('/api/'+name+'?id='+ident,json=body,headers={'X-HA-AI-Studio-Request':'1'})
    async def test_device_snapshot_protects_reference_before_first_execution(self):
        from test_devices import configured_device, sensor
        service=self.studio.get('first')
        asset=await service.assets.upload({'base64':base64.b64encode(PNG).decode()})
        config=copy.deepcopy(service.config['draft']);config['settings']={'reference':asset['id']}
        await service.save(config,False)
        await configured_device(self.studio,[sensor(action='first')])
        config['settings']={};await service.save(config,False)
        self.assertFalse(service.jobs)
        with self.assertRaises(Invalid):await service.assets.delete({'id':asset['id']})
        self.assertTrue(service.assets.path(asset['id']).exists())

    async def test_upload_immutable_private_and_scope(self):
        response=await self.post('action-asset',{'base64':base64.b64encode(PNG).decode()});self.assertEqual(response.status,200)
        value=await response.json();self.assertEqual(value['width'],1)
        response=await self.client.get('/api/action-asset?id=first&asset='+value['id']);self.assertEqual(await response.read(),PNG)
        response=await self.client.get('/api/action-asset?id=second&asset='+value['id']);self.assertEqual(response.status,400)
        response=await self.client.get('/api/action-asset?id=first&asset=../../identity.json');self.assertEqual(response.status,400)
        again=await self.post('action-asset',{'base64':base64.b64encode(PNG).decode()});self.assertEqual((await again.json())['id'],value['id'])
        self.assertEqual(len(list((self.studio.get('first').store.root/'assets').iterdir())),1)
    async def test_preview_has_no_job_config_or_network_mutation(self):
        service=self.studio.get('first');config=copy.deepcopy(service.config);jobs=copy.deepcopy(service.jobs)
        response=await self.post('action-preview',{'data':{},'settings':{}});self.assertEqual(response.status,200,await response.text())
        result=await response.json();self.assertEqual(result['result'],{'mode':'preview'});self.assertEqual(result['delivery'],'not_sent')
        self.assertEqual(service.config,config);self.assertEqual(service.jobs,jobs);self.assertIsNone(service.last);self.assertIsNone(service.mqtt)
        response=await self.client.get('/api/preview-image',params={'id':'first','run':result['run'],'name':'result.png'});self.assertEqual(await response.read(),PNG)
        response=await self.client.get('/api/preview-image',params={'id':'second','run':result['run'],'name':'result.png'});self.assertEqual(response.status,400)
        self.assertEqual(await service.execute({},{}),{'mode':'execute'})
    async def test_invalid_images_requests_and_busy_guards(self):
        for body in ({'base64':'bad%'},{'base64':base64.b64encode(b'<svg></svg>').decode()},{'path':'/etc/passwd'}):
            response=await self.post('action-asset',body);self.assertEqual(response.status,400)
        response=await self.client.post('/api/action-asset?id=first',json={'base64':base64.b64encode(PNG).decode()});self.assertEqual(response.status,403)
        service=self.studio.get('first')
        async with service.lock:
            response=await self.post('action-preview',{'data':{},'settings':{}});self.assertEqual(response.status,400)
        response=await self.post('action-preview',{'data':{},'settings':{},'extra':True});self.assertEqual(response.status,400)
    async def test_preview_failure_cleans_outputs_and_preserves_jobs(self):
        service=self.studio.get('first')
        async def failure(*args):raise Invalid('Synthetic preview failure.')
        with patch.object(service.environment,'preview',failure):
            with self.assertRaises(Invalid):await preview(service,{'data':{},'settings':{}})
        self.assertEqual(list((service.store.root/'previews').iterdir()),[]);self.assertEqual(service.jobs,{})
    def test_image_header_pixel_limit(self):
        import struct
        huge=PNG[:16]+struct.pack('>II',8192,8192)+PNG[24:]
        with self.assertRaises(Invalid):image_info(huge)
    async def test_cancelled_preview_and_protected_asset_deletion(self):
        service=self.studio.get('first')
        before=copy.deepcopy((service.config,service.jobs,service.last))
        async def cancelled(*args):raise asyncio.CancelledError()
        with patch.object(service.environment,'preview',cancelled):
            with self.assertRaises(asyncio.CancelledError):await preview(service,{'data':{},'settings':{}})
        self.assertEqual((service.config,service.jobs,service.last),before)
        self.assertEqual(list((service.store.root/'previews').iterdir()),[])
        value=await service.assets.upload({'base64':base64.b64encode(PNG).decode()})
        service.config['draft']['settings']={'image':value['id']}
        with self.assertRaises(Invalid):await service.assets.delete({'id':value['id']})
        service.config['draft']['settings']={}
        self.assertEqual(await service.assets.delete({'id':value['id']}),{'deleted':True})
    async def test_timed_out_preview_preserves_state(self):
        service=self.studio.get('first');before=copy.deepcopy((service.config,service.jobs,service.last))
        async def timeout(*args):raise asyncio.TimeoutError()
        with patch.object(service.environment,'preview',timeout):
            with self.assertRaises(asyncio.TimeoutError):await preview(service,{'data':{},'settings':{}})
        self.assertEqual((service.config,service.jobs,service.last),before)
        self.assertEqual(list((service.store.root/'previews').iterdir()),[])

    async def test_camera_test_requires_capability_and_preserves_state(self):
        service=self.studio.get('first');before=copy.deepcopy((service.config,service.jobs,service.last))
        response=await self.post('action-camera-test',{'data':{},'settings':{}})
        self.assertEqual(response.status,400)
        service.plugin.manifest['capabilities'].append('home_assistant')
        async def invoke(data,settings,mode,output):
            self.assertEqual(mode,'camera_test')
            (output/'capture.png').write_bytes(PNG)
            return {'camera':True}
        with patch.object(service.environment,'invoke',invoke):
            response=await self.post('action-camera-test',{'data':{},'settings':{}})
        self.assertEqual(response.status,200,await response.text())
        result=await response.json();self.assertEqual(result['delivery'],'not_sent')
        self.assertEqual(result['result'],{'camera':True})
        self.assertEqual((service.config,service.jobs,service.last),before)
        self.assertIsNone(service.mqtt)

    async def test_camera_progress_is_live_scoped_and_preserved_on_failure(self):
        from app.test_progress import current
        service=self.studio.get('first');service.plugin.manifest['capabilities'].append('home_assistant')
        started=asyncio.Event();finish=asyncio.Event();ident='a'*32
        async def invoke(*args):
            current.get()('Fetching camera image.');started.set();await finish.wait()
            raise Invalid('Home Assistant returned HTTP 500 during camera image capture.')
        with patch.object(service.environment,'invoke',invoke):
            task=asyncio.create_task(self.post('action-camera-test',{'data':{},'settings':{},'progress_id':ident}))
            await asyncio.wait_for(started.wait(),3)
            response=await self.client.get('/api/camera-test-progress',params={'id':'first','run':ident})
            self.assertIn('Fetching camera image.',[e['message'] for e in (await response.json())['entries']])
            response=await self.client.get('/api/camera-test-progress',params={'id':'second','run':ident})
            self.assertEqual((await response.json())['entries'],[])
            finish.set();self.assertEqual((await task).status,400)
        response=await self.client.get('/api/camera-test-progress',params={'id':'first','run':ident})
        self.assertIn('HTTP 500',(await response.json())['entries'][-1]['message'])
        self.assertEqual(service.jobs,{})

    async def test_reference_capture_imports_private_assets_without_changing_config(self):
        service=self.studio.get('first');service.plugin.manifest['capabilities'].append('home_assistant')
        before=copy.deepcopy((service.config,service.jobs))
        async def invoke(data,settings,mode,output):
            self.assertEqual(mode,'reference_capture')
            (output/'reference-1.png').write_bytes(PNG)
            (output/'reference-2.png').write_bytes(PNG)
            return {'status':'read'}
        with patch.object(service.environment,'invoke',invoke):
            response=await self.post('action-camera-reference',{'data':{},'settings':{},'progress_id':'b'*32})
        self.assertEqual(response.status,200,await response.text())
        result=await response.json();self.assertEqual(len(result['assets']),2)
        self.assertEqual(service.assets.path(result['assets'][0]['id']).read_bytes(),PNG)
        with self.assertRaises(Invalid):self.studio.get('second').assets.path(result['assets'][0]['id'])
        self.assertEqual((service.config,service.jobs),before)
