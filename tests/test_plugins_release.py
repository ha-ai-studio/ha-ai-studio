"""Synthetic release packages; no GitHub, MQTT or production state access."""
import copy
import asyncio
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from app.actions import Studio
from app.domain import Invalid
from app.releases import ASSET, fetch_release, unpack, repository_url, download


def package(version='1'):
    manifest = {'id':'sample','name':'Sample','description':'Synthetic','version':version,
                'python':f'{sys.version_info.major}.{sys.version_info.minor}',
                'entrypoint':'action.py','settings_schema':{'type':'object'},'editor':'ui/editor.js'}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for name, value in {'action.json':json.dumps(manifest),'action.py':f'def execute(data, settings):\n return {version!r}\n',
                            'requirements.lock':'','ui/editor.js':"import './fields.js'; export function render(h,s){return ()=>s}",
                            'ui/fields.js':'export const version = 1;'}.items():
            archive.writestr(zipfile.ZipInfo('actions/sample/'+name,date_time=(2020,1,1,0,0,0)),value)
    return stream.getvalue()


class Releases(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.studio=Studio(self.root/'data');self.version='1';self.bad=False;self.prerelease=False
        self.urls=[]
        async def fake_download(session,url,limit):
            self.urls.append(url)
            data=package(self.version)
            if url.endswith('.sha256'):
                return ((('0'*64) if self.bad else hashlib.sha256(data).hexdigest())+'  '+ASSET+'\n').encode()
            if url.endswith('.zip'):return data
            return json.dumps({'id':int(self.version),'tag_name':'v'+self.version,'draft':False,'prerelease':self.prerelease,
                'assets':[{'name':name,'state':'uploaded','browser_download_url':'https://github.com/test/repo/releases/download/v'+self.version+'/'+name}
                          for name in (ASSET,ASSET+'.sha256')]}).encode()
        self.mock=patch('app.releases.download',side_effect=fake_download);self.mock.start()
    async def asyncTearDown(self):
        self.mock.stop();await self.studio.close();self.tmp.cleanup()
    async def seed(self):
        repos=self.studio.repositories
        result=await repos.save({'kind':'release','url':'https://github.com/test/repo.git','ref':'latest'})
        self.ident=result['repositories'][0]['id'];await repos.tasks[self.ident]
        self.assertEqual(repos.state['repositories'][0]['status'],'Ready')
        return repos
    async def test_refresh_and_restart_keep_existing_instance_pinned(self):
        repos=await self.seed()
        kind=next(iter(repos.types()))
        await self.studio.create({'id':'sample','name':'Sample','type':kind})
        service=self.studio.get('sample');await service.environment.task
        self.assertTrue(service.environment.ready)
        old=copy.deepcopy(service.plugin.record)
        self.version='2';repos.refresh(self.ident);await repos.tasks[self.ident]
        self.assertEqual(repos.state['repositories'][0]['release']['tag'],'v2')
        self.assertEqual(service.plugin.record,old)
        self.assertEqual(await service.execute({},{}),'1')
        self.assertTrue(service.plugin.source.is_dir())
        restored=Studio(self.root/'data')
        try:
            self.assertEqual(restored.get('sample').plugin.record,old)
            self.assertEqual(await restored.get('sample').execute({},{}),'1')
            self.assertEqual(next(iter(restored.repositories.types().values())).manifest['version'],'2')
        finally:await restored.close()
        self.assertTrue(any(u.endswith('/releases/latest') for u in self.urls))
    async def test_failed_refresh_preserves_catalog(self):
        repos=await self.seed();old=copy.deepcopy(repos.state['repositories'][0]['actions'])
        for bad,pre in ((True,False),(False,True)):
            self.bad=bad;self.prerelease=pre
            repos.refresh(self.ident);await repos.tasks[self.ident]
            self.assertEqual(repos.state['repositories'][0]['status'],'Failed')
            self.assertEqual(repos.state['repositories'][0]['actions'],old)
    async def test_explicit_tag_and_mismatch(self):
        target=self.root/'out';target.mkdir()
        result=await fetch_release('https://github.com/test/repo','v1',target)
        self.assertEqual(result['tag'],'v1')
        self.assertTrue(any(u.endswith('/releases/tags/v1') for u in self.urls))
        with self.assertRaises(Invalid):await fetch_release('https://github.com/test/repo','v2',target)
    async def test_url_and_duplicate_validation(self):
        await self.seed()
        with self.assertRaises(Invalid):await self.studio.repositories.save({'kind':'release','url':'https://github.com/test/repo','ref':'latest'})
        for url in ('http://github.com/a/b','https://user:secret@github.com/a/b','https://example.com/a/b','https://github.com/a/../b','https://github.com/a/b?token=secret'):
            with self.assertRaises(Invalid):repository_url(url)
        with self.assertRaises(Invalid):await download(None,'http://localhost/secret',100)


class Archives(unittest.TestCase):
    def test_unsafe_archives(self):
        for name, mode, data in [('../escape',0,'x'),('/actions/a/file',0,'x'),('actions/a/../escape',0,'x'),
                ('actions/a/.env',0,'secret'),('actions/a/link',stat.S_IFLNK|0o777,'/tmp'),
                ('actions/a/large',0,'x'*(5*1024*1024+1)),('actions\\a\\x',0,'x')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                stream=io.BytesIO()
                with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
                    info=zipfile.ZipInfo(name);info.external_attr=mode<<16;archive.writestr(info,data)
                with self.assertRaises(Invalid):unpack(stream.getvalue(),Path(tmp))


class Downloads(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_validation_and_stream_limit(self):
        class Response:
            def __init__(self,status=200,location=None,chunks=()):
                self.status=status;self.headers={'Location':location};self.chunks=chunks;self.content=self
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def raise_for_status(self):pass
            async def iter_chunked(self,size):
                for chunk in self.chunks:yield chunk
        class Session:
            def __init__(self,responses):self.responses=iter(responses);self.urls=[]
            def get(self,url,**kwargs):self.urls.append(url);return next(self.responses)
        session=Session([Response(302,'http://localhost/private')])
        with self.assertRaises(Invalid):await download(session,'https://github.com/a/b',10)
        self.assertEqual(len(session.urls),1)
        session=Session([Response(chunks=[b'12345',b'67890'])])
        with self.assertRaises(Invalid):await download(session,'https://github.com/a/b',9)
        session=Session([Response(302,'https://release-assets.githubusercontent.com/asset'),Response(chunks=[b'abc'])])
        self.assertEqual(await download(session,'https://github.com/a/b',3),b'abc')

class VersionManagement(Releases):
    async def test_check_is_metadata_only_and_history_survives_without_instances(self):
        from app.plugins import Repositories
        repos=await self.seed();old=copy.deepcopy(repos.state['repositories'][0]['actions'])
        self.version='2';self.urls.clear()
        await repos.check_update(self.ident)
        public=repos.public()['repositories'][0]
        self.assertTrue(public['update_available']);self.assertEqual(public['actions'],old)
        self.assertFalse(any(url.endswith('.zip') for url in self.urls))
        repos.refresh(self.ident);await repos.tasks[self.ident]
        await repos.prune_sources()
        restored=Repositories(self.root/'data');versions=restored.public()['repositories'][0]['versions']
        self.assertEqual(len(versions),2)
        self.assertTrue(all((repos.store.root/'sources'/v['snapshot']).is_dir() for v in versions))
        self.assertEqual(versions[0]['url'],'https://github.com/test/repo/releases/tag/v1')
        repos.refresh(self.ident);await repos.tasks[self.ident]
        self.assertEqual(len(repos.public()['repositories'][0]['versions']),2)
        await repos.check_update(self.ident)
        self.assertFalse(repos.public()['repositories'][0]['update_available'])

    async def test_upgrade_downgrade_preserves_identity_jobs_and_restart(self):
        repos=await self.seed();kind=next(iter(repos.types()))
        await self.studio.create({'id':'sample','name':'Sample','type':kind})
        service=self.studio.get('sample');await service.environment.task
        original=copy.deepcopy(service.plugin.record);identity=copy.deepcopy(service.identity)
        service.config['active']=copy.deepcopy(service.config['draft'])
        result,_=await service.request('{"request_id":"cached","data":false}')
        config=copy.deepcopy(service.config)
        self.version='2';repos.refresh(self.ident);await repos.tasks[self.ident]
        target=next(iter(repos.types().values())).record['snapshot']
        await self.studio.change_version('sample',{'snapshot':target});await service.environment.task
        self.assertEqual(await service.execute(None,{}),'2')
        self.assertEqual(service.identity,identity);self.assertEqual(service.config,config)
        self.assertEqual((await service.request('{"request_id":"cached","data":false}'))[0],result)
        self.assertEqual(service.jobs['cached']['plugin'],original)
        await self.studio.change_version('sample',{'snapshot':original['snapshot']});await service.environment.task
        self.assertEqual(await service.execute(None,{}),'1')
        restored=Studio(self.root/'data')
        try:self.assertEqual(restored.get('sample').plugin.record,original)
        finally:await restored.close()

    async def test_bulk_previews_blocks_and_commits_all_bindings(self):
        repos=await self.seed();kind=next(iter(repos.types()))
        for ident in ('first','second'):
            await self.studio.create({'id':ident,'name':ident,'type':kind})
            await self.studio.get(ident).environment.task
        old=self.studio.get('first').plugin.record['snapshot']
        self.version='2';repos.refresh(self.ident);await repos.tasks[self.ident]
        second=self.studio.get('second');second.config['active']=copy.deepcopy(second.config['draft']);second.config['enabled']=True
        preview=await self.studio.repository_actions({'id':self.ident})
        self.assertIsNone(preview['actions'][1]['reason'])
        targets=[{k:item[k] for k in ('id','snapshot','current')} for item in preview['actions']]
        with patch.object(self.studio.store,'write',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):await self.studio.repository_actions({'id':self.ident,'targets':targets},apply=True)
        self.assertEqual(self.studio.get('first').plugin.record['snapshot'],old)
        await self.studio.repository_actions({'id':self.ident,'targets':targets},apply=True)
        for service in self.studio.services.values():
            await service.environment.task
            self.assertEqual(await service.execute(None,{}),'2')
        with self.assertRaisesRegex(Invalid,'changed'):await self.studio.repository_actions({'id':self.ident,'targets':targets},apply=True)

    async def test_switch_rejects_incompatible_unknown_busy_and_failed_registry(self):
        from app.plugins import GitPlugin
        repos=await self.seed();kind=next(iter(repos.types()))
        await self.studio.create({'id':'sample','name':'Sample','type':kind})
        service=self.studio.get('sample');await service.environment.task
        old=copy.deepcopy(service.plugin.record)
        self.version='2';repos.refresh(self.ident);await repos.tasks[self.ident]
        target=next(iter(repos.types().values())).record['snapshot']
        with self.assertRaises(Invalid):await self.studio.change_version('sample',{'snapshot':'unknown'})
        with self.assertRaisesRegex(Invalid,'version changed'):await service.save(service.config['draft'],False,set_enabled=False,expected_snapshot=target)
        with patch.object(GitPlugin,'validate',side_effect=Invalid('Settings incompatible')):
            with self.assertRaisesRegex(Invalid,'Settings'):await self.studio.change_version('sample',{'snapshot':target})
        with patch.object(self.studio.store,'write',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):await self.studio.change_version('sample',{'snapshot':target})
        self.assertEqual(service.plugin.record,old);self.assertTrue(service.environment.ready)
        from app.plugins import Environment
        with patch.object(Environment,'start',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):await self.studio.change_version('sample',{'snapshot':target})
        self.assertTrue(service.environment.ready)
        self.assertEqual(await service.execute(None,{}),'1')
        async def failed(environment):environment.set('Failed','Synthetic install failure.')
        with patch.object(Environment,'install',failed):
            with self.assertRaisesRegex(Invalid,'unchanged'):await self.studio.change_version('sample',{'snapshot':target})
        self.assertEqual(service.plugin.record,old)
        self.assertEqual(await service.execute(None,{}),'1')

    async def test_active_function_device_follows_upgrade_without_reactivation(self):
        repos=await self.seed();kind=next(iter(repos.types()))
        await self.studio.create({'id':'sample','name':'Sample','type':kind})
        service=self.studio.get('sample');await service.environment.task
        original=service.plugin.record['snapshot']
        device=self.studio.devices.create({'name':'Function device'})
        config={**device['draft'],'functions':[{'id':'execute','name':'Execute sample','action_id':'sample','function':'execute'}]}
        await self.studio.devices.save(device['id'],{'config':config,'enabled':True})
        self.version='2';repos.refresh(self.ident);await repos.tasks[self.ident]
        target=repos.state['repositories'][0]['actions'][0]['snapshot']
        preview=await self.studio.repository_actions({'id':self.ident})
        self.assertIsNone(preview['actions'][0]['reason'])
        self.assertEqual(service.plugin.record['snapshot'],original)
        before=copy.deepcopy(self.studio.devices.get(device['id']))
        await self.studio.change_version('sample',{'snapshot':target});await service.environment.task
        self.assertEqual(self.studio.devices.get(device['id'])['bindings']['sample']['plugin']['snapshot'],original)
        self.assertEqual(self.studio.devices.get(device['id']),before)
        self.studio.devices.invoke_function(device['id'],{'function_id':'execute','request_id':'after-upgrade'})
        await self.studio.devices.tasks[device['id']]
        self.assertEqual(self.studio.devices.get(device['id'])['status'],'ready')
        self.assertEqual(list(service.jobs.values())[-1]['result']['data'],'2')

    async def test_hot_switch_prepares_alongside_execution_drains_and_removes_old_venv(self):
        from app.plugins import Environment
        repos=await self.seed();kind=next(iter(repos.types()))
        await self.studio.create({'id':'sample','name':'Sample','type':kind})
        service=self.studio.get('sample');await service.environment.task
        old_path=service.environment.path
        service.config.update(active=copy.deepcopy(service.config['draft']),enabled=True)
        service.store.write('action-config.json',service.config)
        service.mqtt=object();connection=service.mqtt
        self.version='2';repos.refresh(self.ident);await repos.tasks[self.ident]
        target=next(iter(repos.types().values())).record['snapshot']
        started=asyncio.Event();release=asyncio.Event();ready=asyncio.Event()
        install=Environment.install
        async def prepare(environment):
            started.set();await release.wait();await install(environment);ready.set()
        with patch.object(Environment,'install',prepare):
            task=asyncio.create_task(self.studio.change_version('sample',{'snapshot':target}))
            await asyncio.wait_for(started.wait(),5)
            self.assertEqual((await service.internal_request('during-build',{},service.config['active']))['data'],'1')
            async with service.lock:
                release.set();await asyncio.wait_for(ready.wait(),30)
                self.assertFalse(task.done());self.assertTrue(old_path.is_dir())
                self.assertEqual(service.plugin.manifest['version'],'1')
            await asyncio.wait_for(task,10)
        self.assertTrue(service.enabled);self.assertIs(service.mqtt,connection)
        self.assertFalse(old_path.exists());self.assertEqual(len(list(old_path.parent.iterdir())),1)
        self.assertEqual((await service.internal_request('after-build',{},service.config['active']))['data'],'2')
        self.assertEqual((await service.internal_request('during-build',{},service.config['active']))['data'],'1')
        service.mqtt=None
        restored=Studio(self.root/'data')
        try:
            self.assertTrue(restored.get('sample').enabled)
            self.assertEqual(await restored.get('sample').execute({},{}),'2')
        finally:await restored.close()

    async def test_cancelled_preparation_retains_current_environment(self):
        from app.plugins import Environment
        repos=await self.seed();kind=next(iter(repos.types()))
        await self.studio.create({'id':'sample','name':'Sample','type':kind})
        service=self.studio.get('sample');await service.environment.task
        old_path=service.environment.path
        self.version='2';repos.refresh(self.ident);await repos.tasks[self.ident]
        target=next(iter(repos.types().values())).record['snapshot']
        started=asyncio.Event()
        async def prepare(environment):
            started.set();await asyncio.Event().wait()
        with patch.object(Environment,'install',prepare):
            task=asyncio.create_task(self.studio.change_version('sample',{'snapshot':target}))
            await asyncio.wait_for(started.wait(),5);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(old_path.exists());self.assertFalse(service.version_updating)
        self.assertEqual(await service.execute({},{}),'1')

    async def test_registry_failure_after_replace_retains_restartable_generations(self):
        repos=await self.seed();kind=next(iter(repos.types()))
        await self.studio.create({'id':'sample','name':'Sample','type':kind})
        service=self.studio.get('sample');await service.environment.task
        self.version='2';repos.refresh(self.ident);await repos.tasks[self.ident]
        target=next(iter(repos.types().values())).record['snapshot']
        write=self.studio.store.write
        def interrupted(name,value):
            write(name,value)
            raise OSError('Synthetic directory fsync failure after replacement')
        with patch.object(self.studio.store,'write',side_effect=interrupted):
            with self.assertRaises(OSError):await self.studio.change_version('sample',{'snapshot':target})
        self.assertEqual(await service.execute({},{}),'1')
        self.assertEqual(len(list((service.store.root/'environments').iterdir())),2)
        restored=Studio(self.root/'data')
        try:
            self.assertEqual(await restored.get('sample').execute({},{}),'2')
            self.assertEqual(len(list((service.store.root/'environments').iterdir())),1)
        finally:await restored.close()
