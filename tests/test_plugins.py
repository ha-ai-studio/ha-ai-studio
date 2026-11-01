import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from app.actions import Studio
from app.plugins import Repositories, manifest, process
from app.domain import Invalid

class GitActions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.studio=Studio(self.root/'data')
        self.fixture=self.root/'fixture';self.fixture.mkdir()
        self.data={'id':'sample','name':'Sample','description':'Synthetic test action','version':'1.0','python':f'{sys.version_info.major}.{sys.version_info.minor}',
            'entrypoint':'action.py','settings_schema':{'type':'object','properties':{'factor':{'type':'integer'}},'additionalProperties':False}}
        (self.fixture/'action.json').write_text(json.dumps(self.data));(self.fixture/'requirements.lock').write_text('')
        (self.fixture/'action.py').write_text('import os\ndef execute(data, settings):\n print("hidden-output")\n assert "DEV_MQTT_PASSWORD" not in os.environ\n assert "SUPERVISOR_TOKEN" not in os.environ\n return data\n')
    async def asyncTearDown(self):
        await self.studio.close();self.tmp.cleanup()
    async def test_device_function_manifest_declaration_and_legacy_readings_contract(self):
        from app.plugins import GitPlugin
        for functions in (None, {}, 'read', ['unknown'], ['execute', 'execute'], [True]):
            (self.fixture/'action.json').write_text(json.dumps({**self.data, 'functions': functions}))
            with self.assertRaises(Invalid):await manifest(self.fixture)
        for extra, expected in [({'functions':['execute']},['execute']), ({'output_schema':{'type':'object','properties':{'readings':{'type':'array'}}}},['execute']), ({'functions':[], 'output_schema':{'properties':{'readings':{}}}},[])]:
            (self.fixture/'action.json').write_text(json.dumps({**self.data, **extra}))
            record={'type':'sample','repository_id':'test','commit':'a'*40,'manifest':await manifest(self.fixture)}
            self.assertEqual(GitPlugin(record,self.fixture).metadata['functions'],expected)

    async def seed(self):
        import shutil
        dest=self.studio.repositories.store.root/'sources'/'snapshot'/'actions'/'sample';dest.parent.mkdir(parents=True);shutil.copytree(self.fixture,dest)
        record={'type':'git-test-sample','repository_id':'test','commit':'a'*40,'snapshot':'snapshot','directory':'actions/sample','manifest':await manifest(dest)}
        state={'repositories':[{'id':'test','url':'https://example.invalid/actions.git','ref':'main','status':'Ready','actions':[record],'logs':[]}]}
        self.studio.repositories.store.write('catalog.json',state);self.studio.repositories.state=state
        await self.studio.create({'id':'sample','name':'Sample instance','type':record['type']})
        return self.studio.get('sample')
    async def test_real_environment_json_values_restart_dedup_and_pinning(self):
        with patch.dict(os.environ,{'DEV_MQTT_PASSWORD':'secret','SUPERVISOR_TOKEN':'token'}):
            s=await self.seed();await s.environment.task
            self.assertEqual(s.environment.state['status'],'Ready',s.environment.state)
            for i,data in enumerate([None,False,0,1.5,'text',[],{},[1,{'x':True}]]):
                self.assertEqual(await s.execute(data,{}),data)
            conf=copy.deepcopy(s.config['draft']);conf['settings']={'factor':2}
            s.config['active']=conf
            first,_=await s.request('{"request_id":"one","data":{"x":1}}')
            self.assertEqual(first['status'],'success')
            self.assertEqual(s.jobs['one']['plugin']['commit'],'a'*40)
            with patch.object(s.environment,'execute',side_effect=AssertionError('must not rerun')):
                self.assertEqual((await s.request('{"request_id":"one","data":{"x":1}}'))[0],first)
                conflict,_=await s.request('{"request_id":"one","data":2}')
                self.assertEqual(conflict['error']['code'],'request_id_conflict')
            # Refresh of a catalog must not change a previously created instance.
            self.studio.repositories.state['repositories'][0]['actions'][0]['commit']='b'*40
            self.assertEqual(s.plugin.record['commit'],'a'*40)
            restored=Studio(self.root/'data')
            self.assertTrue(restored.get('sample').environment.ready)
            self.assertEqual(await restored.get('sample').execute(False,{}),False)
            await restored.close()
    async def test_activation_preview_and_rebuild_guards(self):
        s=await self.seed()
        with self.assertRaises(Invalid):s.environment.require()
        with self.assertRaises(Invalid):await s.save(s.config['draft'],True)
        with self.assertRaises(Invalid):await s.test(s.config['draft'],'{}')
        self.assertFalse(s.jobs);await s.environment.task
        s.environment.set('Installing','Synthetic interrupted install')
        restored=Studio(self.root/'data');self.assertEqual(restored.get('sample').environment.state['status'],'Failed');await restored.close()
    async def test_install_failure_and_recovery(self):
        (self.fixture/'requirements.lock').write_text('unlocked-package\n')
        s=await self.seed();await s.environment.task
        self.assertEqual(s.environment.state['status'],'Failed')
        with self.assertRaises(Invalid):await s.execute({}, {})
        (s.plugin.source/'requirements.lock').write_text('');s.environment.start();await s.environment.task
        self.assertTrue(s.environment.ready)
        (s.environment.path/'bin/python').unlink()
        restored=Studio(self.root/'data');self.assertEqual(restored.get('sample').environment.state['status'],'Failed');await restored.close()
    async def test_venv_path_applies_to_verification_and_child_processes(self):
        (self.fixture/'action.py').write_text("""import os, shutil, subprocess, sys
from pathlib import Path
assert os.environ['VIRTUAL_ENV'] == sys.prefix
assert shutil.which('pip') == str(Path(sys.prefix)/'bin/pip')
def execute(data, settings):
    return {'prefix':subprocess.check_output(['python3','-c','import sys;print(sys.prefix)'],text=True).strip(),
            'helper':subprocess.check_output(['review-helper'],text=True)}
""")
        s=await self.seed();await s.environment.task
        self.assertTrue(s.environment.ready,s.environment.state)
        helper=s.environment.path/'bin/review-helper';helper.write_text("#!/bin/sh\nprintf 'venv-helper'\n");helper.chmod(0o755)
        value=await s.execute(None,{})
        self.assertEqual(value,{'prefix':str(s.environment.path),'helper':'venv-helper'})

    async def test_message_metadata_uses_isolated_manifest_data_schemas(self):
        from app.plugins import GitPlugin,SCHEMA,OUTPUT
        from jsonschema import Draft202012Validator
        record={'type':'review','repository_id':'test','commit':'a'*40,'manifest':{**self.data,
            'input_schema':{'type':'integer'},'output_schema':{'type':'object','required':['answer'],'properties':{'answer':{'type':'string'}}}}}
        plugin=GitPlugin(record,self.fixture)
        self.assertEqual(plugin.metadata['input_schema']['properties']['data'],{'type':'integer'})
        self.assertTrue(Draft202012Validator(plugin.metadata['input_schema']).is_valid({'request_id':'one','data':42}))
        self.assertFalse(Draft202012Validator(plugin.metadata['input_schema']).is_valid({'request_id':'one','data':'wrong'}))
        self.assertFalse(Draft202012Validator(plugin.metadata['output_schema']).is_valid({'request_id':'one','status':'success','data':{}}))
        plugin.metadata['input_schema']['properties']['data']['type']='string'
        self.assertEqual(SCHEMA['properties']['data'],{});self.assertEqual(OUTPUT['properties']['data'],{})
        self.assertEqual(record['manifest']['input_schema'],{'type':'integer'})
        other=GitPlugin(record,self.fixture);self.assertEqual(other.metadata['input_schema']['properties']['data'],{'type':'integer'})

    async def test_timeout_and_output_limit(self):
        result=await asyncio.wait_for(process([sys.executable,'-c','import sys;print(len(sys.stdin.buffer.read()),end="")'],payload=b'x'*120000,capture=True),3)
        self.assertEqual(result,b'120000')
        result=await asyncio.wait_for(process([sys.executable,'-c','import sys;sys.stdout.write("x"*65536)'],capture=True),3)
        self.assertEqual(len(result),65536)
        with self.assertRaises(asyncio.TimeoutError):await process([sys.executable,'-c','import time;time.sleep(30)'],timeout=.05)
        for size in (70000,1000000,10000000):
            with self.subTest(size=size),self.assertRaisesRegex(Invalid,'65536 bytes'):
                await asyncio.wait_for(process([sys.executable,'-c',f'print("x"*{size})'],capture=True),3)
    async def test_large_action_result_fails_promptly_and_next_request_executes(self):
        (self.fixture/'action.py').write_text("def execute(data,settings):\n return 'x'*1000000 if data else data\n")
        s=await self.seed();await s.environment.task;self.assertTrue(s.environment.ready)
        s.config['active']=copy.deepcopy(s.config['draft'])
        async with s.lock:
            result,_=await asyncio.wait_for(s.request('{"request_id":"large","data":true}'),5)
        self.assertEqual(result['error']['code'],'execution_failed');self.assertFalse(s.busy)
        async with s.lock:
            result,_=await asyncio.wait_for(s.request('{"request_id":"next","data":false}'),5)
        self.assertEqual(result,{'request_id':'next','status':'success','data':False})

    async def test_cancelled_capture_reaps_process(self):
        pidfile=self.root/'process.pid'
        code=f"import os,time;open({str(pidfile)!r},'w').write(str(os.getpid()));print('ready',flush=True);time.sleep(30)"
        task=asyncio.create_task(process([sys.executable,'-c',code],capture=True))
        try:
            for _ in range(200):
                if pidfile.exists() and pidfile.stat().st_size:break
                await asyncio.sleep(.01)
            self.assertTrue(pidfile.exists());pid=int(pidfile.read_text())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,3)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_escaped_descendant_cannot_hold_cleanup_open(self):
        import signal
        pidfile=self.root/'descendant.pid'
        code=f"import subprocess,sys;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True);open({str(pidfile)!r},'w').write(str(p.pid));print('parent result',flush=True)"
        try:
            result=await asyncio.wait_for(process([sys.executable,'-c',code],capture=True),4)
            self.assertEqual(result,b'parent result\n')
        finally:
            if pidfile.exists():
                try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
                except ProcessLookupError:pass

    async def test_installation_rejects_execute_with_wrong_signature(self):
        (self.fixture/'action.py').write_text('def execute():\n return 42\n')
        s=await self.seed();await s.environment.task
        self.assertEqual(s.environment.state['status'],'Failed');self.assertFalse(s.environment.ready)

    async def test_runner_checks_signature_without_calling_execute(self):
        from app.plugins import RUNNER
        entry=self.fixture/'action.py';sentinel=self.root/'executed'
        for signature in ('','data,settings,extra','*,data,settings'):
            entry.write_text(f'def execute({signature}):\n return 42\n')
            with self.assertRaises(Invalid):
                await process([sys.executable,'-I',RUNNER,self.fixture,'action.py'],payload=b'{"mode":"check"}',capture=True)
        for prefix,signature in (('','data,settings,/'),('async ','data,settings'),('','data,settings,optional=None')):
            entry.write_text(f"{prefix}def execute({signature}):\n open({str(sentinel)!r},'w').write('unexpected')\n return 42\n")
            result=await process([sys.executable,'-I',RUNNER,self.fixture,'action.py'],payload=b'{"mode":"check"}',capture=True)
            self.assertEqual(json.loads(result),{'ready':True});self.assertFalse(sentinel.exists())

    async def test_request_examples_are_validated_or_unavailable(self):
        from app.plugins import GitPlugin,valid_example
        data={**self.data,'input_schema':{'type':'integer'}}
        (self.fixture/'action.json').write_text(json.dumps(data));accepted=await manifest(self.fixture)
        record={'type':'review','repository_id':'test','commit':'a'*40,'manifest':accepted}
        self.assertIsNone(GitPlugin(record,self.fixture).metadata['example'])
        for example in [None,{}, {'request_id':'one','data':{}},{'request_id':' ','data':42},{'request_id':'\n','data':42},{'request_id':'one','data':42,'extra':True}]:
            (self.fixture/'action.json').write_text(json.dumps({**data,'example':example}))
            with self.assertRaisesRegex(Invalid,'Example must be a valid request'):await manifest(self.fixture)
        example={'request_id':'one','data':0}
        (self.fixture/'action.json').write_text(json.dumps({**data,'example':example}));record['manifest']=await manifest(self.fixture)
        self.assertEqual(GitPlugin(record,self.fixture).metadata['example'],example)
        for value in [None,False,0,'text',[],{}]:self.assertTrue(await valid_example({'request_id':'one','data':value},{}))
        for value in ['x'*17000,float('nan'),'\ud800']:self.assertFalse(await valid_example({'request_id':'one','data':value},{}))
        record['manifest']={**data,'example':{'request_id':'one','data':{}}}
        self.assertFalse(await valid_example(record['manifest']['example'],data['input_schema']))

    async def test_manifest_rejects_external_references_and_path_escape(self):
        for change in [{'entrypoint':'../action.py'},{'settings_schema':{'type':'object','$ref':'https://example.invalid/schema'}},{'python':'0.0'},{'id':'../../x'}]:
            (self.fixture/'action.json').write_text(json.dumps({**self.data,**change}))
            with self.assertRaises(Invalid):await manifest(self.fixture)
    async def test_sources_validate_and_refresh_failures_preserve_catalog(self):
        for url in ['file:///tmp/repo','http://example.com/repo','https://user:secret@example.com/repo','ssh://example.com/repo']:
            with self.assertRaises(Invalid):await self.studio.repositories.save({'url':url,'ref':'main'})
        s=await self.seed()
        with patch('app.plugins.process',side_effect=RuntimeError('private-secret')):
            await self.studio.repositories.sync('test')
        public=self.studio.repositories.public();self.assertEqual(public['repositories'][0]['status'],'Failed')
        self.assertEqual(len(self.studio.repositories.types()),1);self.assertNotIn('private-secret',json.dumps(public))
        await s.environment.task

    async def test_real_git_catalog_multiple_actions_no_import_and_duplicate_ids(self):
        import shutil
        repo=self.root/'git-fixture';repo.mkdir()
        for name in ('first','second'):
            dest=repo/'actions'/name;dest.parent.mkdir(exist_ok=True);shutil.copytree(self.fixture,dest)
            data={**self.data,'id':name};(dest/'action.json').write_text(json.dumps(data))
            (dest/'action.py').write_text('raise RuntimeError("catalog must not import code")')
        await process(['git','init','--quiet',repo])
        await process(['git','-C',repo,'add','.'])
        async def commit():
            await process(['git','-C',repo,'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','--quiet','-am','Synthetic fixture'])
        await commit()
        real_process=process
        async def redirect_fetch(args,**kwargs):
            args=list(args)
            if 'fetch' in args:
                args[args.index('protocol.file.allow=never')]='protocol.file.allow=always'
                args[-2]=str(repo)
            return await real_process(args,**kwargs)
        with patch('app.plugins.process',side_effect=redirect_fetch):
            result=await self.studio.repositories.save({'url':'https://example.invalid/actions.git','ref':'HEAD'})
            ident=result['repositories'][0]['id'];await self.studio.repositories.tasks[ident]
            current=self.studio.repositories.public()['repositories'][0]
            self.assertEqual(current['status'],'Ready',current)
            self.assertEqual(len(current['actions']),2)
            before=copy.deepcopy(current['actions'])
            (repo/'actions'/'second'/'action.json').write_text(json.dumps({**self.data,'id':'first'}))
            await commit();self.studio.repositories.refresh(ident);await self.studio.repositories.tasks[ident]
            current=self.studio.repositories.public()['repositories'][0]
            self.assertEqual(current['status'],'Failed');self.assertEqual(current['actions'],before)

    async def test_hashed_dependency_wheel_install_and_wrong_hash(self):
        import functools
        import hashlib
        import http.server
        import threading
        import zipfile
        index=self.root/'index';package=index/'simple'/'synthetic-dependency';package.mkdir(parents=True)
        wheel=package/'synthetic_dependency-1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel,'w') as archive:
            archive.writestr('synthetic_dependency.py','VALUE = 42\n')
            archive.writestr('synthetic_dependency-1.0.dist-info/METADATA','Metadata-Version: 2.1\nName: synthetic-dependency\nVersion: 1.0\n')
            archive.writestr('synthetic_dependency-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: synthetic-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            archive.writestr('synthetic_dependency-1.0.dist-info/RECORD','')
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(index)))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        sha=hashlib.sha256(wheel.read_bytes()).hexdigest()
        (self.fixture/'requirements.lock').write_text(f'synthetic-dependency==1.0 --hash=sha256:{sha}\n')
        (self.fixture/'action.py').write_text('from synthetic_dependency import VALUE\ndef execute(data, settings):\n return VALUE\n')
        real_process=process
        async def test_index(args,**kwargs):
            args=list(args)
            if 'pip' in args and 'install' in args:args+=['--index-url',f'http://127.0.0.1:{server.server_port}/simple/','--no-cache-dir']
            return await real_process(args,**kwargs)
        try:
            with patch('app.plugins.process',side_effect=test_index):
                s=await self.seed();await s.environment.task
                self.assertTrue(s.environment.ready,s.environment.state)
                self.assertEqual(await s.execute(None,{}),42)
                (s.plugin.source/'requirements.lock').write_text('synthetic-dependency==1.0 --hash=sha256:'+'0'*64+'\n')
                s.environment.start();await s.environment.task
                self.assertEqual(s.environment.state['status'],'Failed')
        finally:
            await asyncio.to_thread(server.shutdown);server.server_close();thread.join()

    async def test_ready_persistence_failure_prevents_execution(self):
        s=await self.seed();await s.environment.task
        original=s.environment.store.write
        def fail_ready(name,value):
            if name=='environment.json' and value['status']=='Ready':raise OSError('synthetic disk full')
            return original(name,value)
        with patch.object(s.environment.store,'write',side_effect=fail_ready):
            s.environment.start();await s.environment.task
        self.assertEqual(s.environment.state['status'],'Failed')
        with self.assertRaises(Invalid):await s.execute(None,{})
        self.assertNotIn('synthetic disk full',json.dumps(s.status()))

    async def test_output_schema_and_interrupted_job_never_rerun(self):
        self.data['output_schema']={'type':'integer'}
        (self.fixture/'action.json').write_text(json.dumps(self.data))
        s=await self.seed();await s.environment.task;s.config['active']=copy.deepcopy(s.config['draft'])
        result,_=await s.request('{"request_id":"bad-output","data":{}}')
        self.assertEqual(result['error']['code'],'execution_failed')
        from app.actions import digest
        body={'request_id':'interrupted','data':42}
        s.jobs['interrupted']={'digest':digest(body),'config':s.config['active'],'execution':'processing','delivery':'not_sent','result':None,'plugin':s.plugin.record}
        s.store.write('action-jobs.json',s.jobs)
        restored=Studio(self.root/'data');service=restored.get('sample')
        with patch.object(service.environment,'execute',side_effect=AssertionError('must not rerun')):
            result,_=await service.request(json.dumps(body))
        self.assertEqual(result['error']['code'],'interrupted');await restored.close()


class DefaultRepository(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_persists_once_and_only_refreshes_metadata(self):
        from app.plugins import DEFAULT_REPOSITORY_URL
        with tempfile.TemporaryDirectory() as root:
            repositories=Repositories(root)
            with patch.object(repositories,'refresh') as refresh:
                repositories.start();first=repositories.public()['repositories']
                self.assertEqual(len(first),1)
                self.assertEqual(first[0]['url'],DEFAULT_REPOSITORY_URL)
                self.assertEqual(first[0]['ref'],'main')
                refresh.assert_called_once_with(first[0]['id'])
            restored=Repositories(root)
            with patch.object(restored,'refresh'):
                restored.start();self.assertEqual(restored.public()['repositories'],first)
            await restored.update(first[0]['id'],status='Ready')
            with patch.object(restored,'refresh') as refresh:
                restored.start();refresh.assert_not_called()
            self.assertEqual(Studio(root).services,{})

    async def test_existing_url_without_git_suffix_and_ref_are_preserved(self):
        from app.plugins import DEFAULT_REPOSITORY_URL
        with tempfile.TemporaryDirectory() as root:
            repositories=Repositories(root)
            original={'id':'existing','url':DEFAULT_REPOSITORY_URL.removesuffix('.git'),'ref':'v0.0.1','status':'Ready','actions':[],'logs':[]}
            repositories.state={'repositories':[original]};repositories.store.write('catalog.json',repositories.state)
            with patch.object(repositories,'refresh') as refresh:
                repositories.start();refresh.assert_not_called()
            public=repositories.public()['repositories']
            self.assertEqual(len(public),1)
            self.assertEqual({key:public[0][key] for key in original},original)
            self.assertTrue(public[0]['system']);self.assertFalse(public[0]['can_delete'])

    async def test_failed_persistence_never_starts_fetch(self):
        with tempfile.TemporaryDirectory() as root:
            repositories=Repositories(root)
            with patch.object(repositories.store,'write',side_effect=OSError('Synthetic failure')),patch.object(repositories,'refresh') as refresh:
                with self.assertRaises(OSError):repositories.start()
                refresh.assert_not_called()
            self.assertEqual(repositories.public()['repositories'],[])


class RepositoryDeletion(unittest.IsolatedAsyncioTestCase):
    def repository(self,id='custom',url='https://example.invalid/actions.git'):
        return {'id':id,'url':url,'ref':'main','status':'Ready','actions':[],'logs':[]}

    async def test_system_sources_are_marked_and_cannot_be_deleted(self):
        from app.plugins import DEFAULT_REPOSITORY_URL
        with tempfile.TemporaryDirectory() as root:
            r=Repositories(root)
            for url in [DEFAULT_REPOSITORY_URL,DEFAULT_REPOSITORY_URL.removesuffix('.git')+'/',DEFAULT_REPOSITORY_URL.upper()]:
                r.state={'repositories':[self.repository(url=url)]}
                self.assertTrue(r.public()['repositories'][0]['system'])
                self.assertFalse(r.public()['repositories'][0]['can_delete'])
                with self.assertRaisesRegex(Invalid,'System repositories'):await r.delete({'id':'custom','confirmed':True})

    async def test_inactive_and_draft_action_bindings_block_removal_after_catalog_refresh(self):
        from app.store import Store
        with tempfile.TemporaryDirectory() as root:
            r=Repositories(root);r.state={'repositories':[self.repository()]};r.store.write('catalog.json',r.state)
            Store(root).write('agents.json',{'agents':[{'id':'inactive','plugin':{'repository_id':'custom','snapshot':'old-version'}}]})
            public=r.public()['repositories'][0];self.assertTrue(public['in_use']);self.assertFalse(public['can_delete'])
            with self.assertRaisesRegex(Invalid,'existing actions'):await r.delete({'id':'custom','confirmed':True})
            self.assertEqual(len(Repositories(root).state['repositories']),1)

    async def test_unused_removal_persists_and_preserves_deleted_action_history(self):
        from app.store import Store
        with tempfile.TemporaryDirectory() as root:
            r=Repositories(root);r.state={'repositories':[self.repository()]};r.store.write('catalog.json',r.state)
            history=r.store.root/'sources'/'historical';history.mkdir(parents=True);(history/'action.py').write_text('# preserved')
            unused=r.store.root/'sources'/'unused';unused.mkdir()
            Store(root).write('agents.json',{'agents':[],'deleted':[{'id':'old','plugin':{'repository_id':'custom','snapshot':'historical'}}]})
            self.assertTrue(r.public()['repositories'][0]['can_delete'])
            self.assertEqual((await r.delete({'id':'custom','confirmed':True}))['repositories'],[])
            self.assertEqual(Repositories(root).state['repositories'],[])
            self.assertTrue((history/'action.py').exists());self.assertFalse(unused.exists())

    async def test_refresh_in_progress_and_persistence_failure_block_removal(self):
        with tempfile.TemporaryDirectory() as root:
            r=Repositories(root);r.state={'repositories':[self.repository()]};r.store.write('catalog.json',r.state)
            r.tasks['custom']=asyncio.create_task(asyncio.Event().wait())
            try:
                self.assertFalse(r.public()['repositories'][0]['can_delete'])
                with self.assertRaisesRegex(Invalid,'refresh'):await r.delete({'id':'custom','confirmed':True})
            finally:await r.close()
            with patch.object(r.store,'write',side_effect=OSError('Synthetic failure')):
                with self.assertRaises(OSError):await r.delete({'id':'custom','confirmed':True})
            self.assertEqual(len(r.state['repositories']),1)
            self.assertEqual(len(Repositories(root).state['repositories']),1)
            for value in [None,{}, {'id':'custom','confirmed':False},{'id':[],'confirmed':True}]:
                with self.assertRaises(Invalid):await r.delete(value)

    async def test_studio_serializes_source_deletion_with_instance_creation(self):
        with tempfile.TemporaryDirectory() as root:
            studio=Studio(root);r=studio.repositories;r.state={'repositories':[self.repository()]}
            async with studio.lock:
                task=asyncio.create_task(studio.delete_repository({'id':'custom','confirmed':True}))
                await asyncio.sleep(0);self.assertFalse(task.done())
            self.assertEqual((await task)['repositories'],[]);await studio.close()
