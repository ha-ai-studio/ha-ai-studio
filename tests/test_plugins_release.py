"""Synthetic release packages; no GitHub, MQTT or production state access."""
import copy
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
        self.assertIn('Deactivate',preview['actions'][1]['reason'])
        targets=[{k:item[k] for k in ('id','snapshot','current')} for item in preview['actions']]
        with self.assertRaises(Invalid):await self.studio.repository_actions({'id':self.ident,'targets':targets},apply=True)
        self.assertEqual(self.studio.get('first').plugin.record['snapshot'],old)
        second.config['enabled']=False
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
        with patch.object(self.studio.devices,'references',return_value=True):
            with self.assertRaisesRegex(Invalid,'Deactivate devices'):await self.studio.change_version('sample',{'snapshot':target})
        with self.assertRaisesRegex(Invalid,'version changed'):await service.save(service.config['draft'],False,set_enabled=False,expected_snapshot=target)
        async with service.lock:
            with self.assertRaisesRegex(Invalid,'busy'):await self.studio.change_version('sample',{'snapshot':target})
        with patch.object(GitPlugin,'validate',side_effect=Invalid('Settings incompatible')):
            with self.assertRaisesRegex(Invalid,'Settings'):await self.studio.change_version('sample',{'snapshot':target})
        with patch.object(self.studio.store,'write',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):await self.studio.change_version('sample',{'snapshot':target})
        self.assertEqual(service.plugin.record,old);self.assertTrue(service.environment.ready)
        with patch.object(service.environment,'start',side_effect=OSError('synthetic')):
            result=await self.studio.change_version('sample',{'snapshot':target})
            self.assertEqual(result['plugin']['snapshot'],target)
            self.assertEqual(result['environment']['status'],'Failed')
        self.assertFalse(service.environment.ready)
        with self.assertRaises(Invalid):await service.execute(None,{})
