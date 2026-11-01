"""Plugin templates are validated snapshot data, with no execution or publication."""
import copy
import io
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from aiohttp.test_utils import TestClient, TestServer
from app.actions import Studio
from app.domain import Invalid
from app.plugins import Repositories, device_templates
from app.releases import unpack
from app.server import create_app
import test_local_plugins
from test_devices import sensor


def template(name='Water meter'):
    return {'format':'ha-ai-studio-device-template','version':1,'config':{
        'name':name,'interval_minutes':30,'publish':False,
        'sensors':[sensor(action='action-1')], 'functions':[]}}


class PluginTemplates(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_local_plugins.LocalActions.asyncSetUp
    asyncTearDown = test_local_plugins.LocalActions.asyncTearDown

    def write_template(self, value=None):
        folder=self.source/'device_templates';folder.mkdir(exist_ok=True)
        (folder/'water.json').write_text(json.dumps(template() if value is None else value))

    async def sync(self):
        repos=self.studio.repositories
        if not repos.state['repositories']:
            result=await repos.save({'kind':'local','path':'.'})
            self.repo_id=result['repositories'][0]['id']
        else:repos.refresh(self.repo_id)
        await repos.tasks[self.repo_id]
        return repos.state['repositories'][0]

    async def test_snapshot_refresh_restart_and_inactive_import(self):
        self.write_template();repo=await self.sync()
        self.assertEqual(repo['status'],'Ready')
        repos=self.studio.repositories;entries=repos.device_templates();old=copy.deepcopy(entries)
        self.assertEqual(len(entries),1)
        self.assertTrue((repos.store.root/'sources'/entries[0]['snapshot']/'device_templates/water.json').is_file())
        self.write_template(template('Changed'))
        self.assertEqual(repos.device_templates(),old)
        restored=Repositories(self.root/'data');self.assertEqual(restored.device_templates(),old)
        repo=await self.sync();self.assertEqual(repo['status'],'Ready')
        self.assertEqual(repos.device_templates()[0]['template']['config']['name'],'Changed')
        self.assertNotEqual(repos.device_templates()[0]['snapshot'],old[0]['snapshot'])
        kind=next(iter(repos.types()))
        await self.studio.create({'id':'sample','name':'Sample','type':kind})
        await self.studio.get('sample').environment.task
        device=self.studio.devices.import_template({'template':old[0]['template'],'name':'Imported','actions':{'action-1':'sample'}})
        self.assertFalse(device['enabled']);self.assertFalse(device['draft']['publish'])
        self.assertIsNone(device['active']);self.assertIsNone(device['next_run']);self.assertEqual(device['values'],{})
        self.assertEqual(device['draft']['sensors'][0]['action_id'],'sample')
        (self.source/'device_templates/water.json').unlink();await self.sync()
        self.assertEqual(repos.device_templates(),[])
        self.assertEqual(self.studio.devices.public(device['id'])['draft'],device['draft'])

    async def test_failed_refresh_keeps_catalog_and_deletion_removes_templates(self):
        self.write_template();await self.sync();repos=self.studio.repositories;old=copy.deepcopy(repos.device_templates())
        for value in ([], True, 1, 'text', {}, {**template(),'version':True}, {**template(),'extra':'private'},
                      {**template(),'config':{**template()['config'],'publish':True}}):
            self.write_template(value);repo=await self.sync()
            self.assertEqual(repo['status'],'Failed');self.assertEqual(repos.device_templates(),old)
        await repos.delete({'id':self.repo_id,'confirmed':True})
        self.assertEqual(repos.device_templates(),[])

    async def test_http_catalog_and_namespace(self):
        self.write_template();await self.sync()
        repo=copy.deepcopy(self.studio.repositories.state['repositories'][0]);repo['id']='other'
        self.studio.repositories.state['repositories'].append(repo)
        entries=self.studio.repositories.device_templates()
        self.assertNotEqual(entries[0]['id'],entries[1]['id'])
        self.studio.repositories.store.write('catalog.json',self.studio.repositories.state)
        app=create_app(self.root/'data',dev=True);app.cleanup_ctx.clear()
        client=TestClient(TestServer(app));await client.start_server()
        try:
            response=await client.get('/api/device-templates',headers={'Host':'localhost'})
            self.assertEqual(response.status,200)
            self.assertEqual((await response.json())['templates'],entries)
        finally:await client.close()

    async def test_local_hidden_files_and_single_action_source(self):
        self.write_template()
        (self.source/'device_templates/.DS_Store').write_text('private metadata')
        (self.source/'device_templates/.env').write_text('private credentials')
        repo=await self.sync();self.assertEqual(repo['status'],'Ready')
        folder=self.studio.repositories.store.root/'sources'/repo['template_snapshot']/'device_templates'
        self.assertEqual([p.name for p in folder.iterdir()],['water.json'])
        shutil.move(str(self.source/'device_templates'),str(self.action/'device_templates'))
        repos=self.studio.repositories
        result=await repos.save({'kind':'local','path':'actions/sample'})
        ident=result['repositories'][-1]['id'];await repos.tasks[ident]
        self.assertEqual(repos.state['repositories'][-1]['status'],'Ready')
        self.assertEqual(len(repos.device_templates()),2)

    async def test_release_sync_and_failed_refresh_preserve_templates(self):
        from test_plugins_release import package
        repos=self.studio.repositories
        invalid=False
        async def release(url, ref, target):
            stream=io.BytesIO(package())
            with zipfile.ZipFile(stream,'a') as archive:
                archive.writestr('device_templates/water.json',json.dumps({} if invalid else template()))
            unpack(stream.getvalue(),target)
            return {'sha256':'b'*64 if invalid else 'a'*64,'tag':'v1','release_id':1}
        with patch('app.plugins.fetch_release',side_effect=release):
            result=await repos.save({'kind':'release','url':'https://github.com/test/repo','ref':'latest'})
            ident=result['repositories'][0]['id'];await repos.tasks[ident]
            self.assertEqual(repos.state['repositories'][0]['status'],'Ready')
            old=repos.device_templates();self.assertEqual(old[0]['template'],template())
            self.assertEqual(Repositories(self.root/'data').device_templates(),old)
            invalid=True;repos.refresh(ident);await repos.tasks[ident]
            self.assertEqual(repos.state['repositories'][0]['status'],'Failed')
            self.assertEqual(repos.device_templates(),old)

    async def test_git_sync_uses_the_same_validated_catalog(self):
        self.write_template();repos=self.studio.repositories
        async def fake_process(args, **kwargs):
            if args[1:3]==['init','--quiet']:shutil.copytree(self.source,Path(args[-1]),dirs_exist_ok=True)
            return b'a'*40 if 'rev-parse' in args else b''
        with patch('app.plugins.process',side_effect=fake_process), patch('app.plugins.manifest',return_value=self.data):
            result=await repos.save({'url':'https://example.invalid/plugins.git','ref':'main'})
            await repos.tasks[result['repositories'][0]['id']]
        self.assertEqual(repos.state['repositories'][0]['status'],'Ready')
        self.assertEqual(repos.device_templates()[0]['template'],template())


class TemplateFiles(unittest.TestCase):
    def test_paths_sizes_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'device_templates';folder.mkdir()
            for filename in ('bad.txt','UPPER.json','private.env'):
                path=folder/filename;path.write_text('{}')
                with self.assertRaises(Invalid):device_templates(root)
                path.unlink()
            path=folder/'water.json';path.write_text(' ' * 80001)
            with self.assertRaises(Invalid):device_templates(root)
            path.unlink();path.symlink_to(root/'missing')
            with self.assertRaises(Invalid):device_templates(root)
            path.unlink()
            for i in range(65):(folder/f'template-{i}.json').write_text(json.dumps(template()))
            with self.assertRaises(Invalid):device_templates(root)

    def test_release_archive_accepts_template_folder_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=io.BytesIO()
            with zipfile.ZipFile(data,'w') as archive:
                archive.writestr('device_templates/water.json',json.dumps(template()))
            unpack(data.getvalue(),root)
            self.assertEqual(device_templates(root)[0]['template'],template())
            for name in ('device_templates/../secret.json','device_templates/.env','other/water.json'):
                data=io.BytesIO()
                with zipfile.ZipFile(data,'w') as archive:archive.writestr(name,'{}')
                with self.assertRaises(Invalid):unpack(data.getvalue(),root)
