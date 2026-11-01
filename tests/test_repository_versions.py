"""Metadata checks and history migration use synthetic source identities only."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.plugins import Repositories
from app.store import Store

class RepositoryVersions(unittest.IsolatedAsyncioTestCase):
    async def test_git_annotated_tag_uses_peeled_commit_and_check_does_not_fetch(self):
        with tempfile.TemporaryDirectory() as root:
            repos=Repositories(root)
            repo={'id':'fixture','url':'https://example.invalid/repo.git','ref':'refs/tags/v1','status':'Ready','actions':[],'logs':[],'commit':'a'*40}
            repos.state={'repositories':[repo]}
            with patch('app.plugins.process',return_value=(('b'*40+'\trefs/tags/v1\n'+'a'*40+'\trefs/tags/v1^{}\n').encode())) as command:
                await repos.check_update('fixture')
                self.assertIn('ls-remote',command.call_args.args[0])
            self.assertFalse(repos.state['repositories'][0]['update_available'])
            self.assertEqual(repos.state['repositories'][0]['actions'],[])
            with patch('app.plugins.process',side_effect=RuntimeError('private diagnostic')):
                await repos.check_update('fixture')
            self.assertNotIn('private diagnostic',str(repos.public()))
            self.assertIsNotNone(repos.state['repositories'][0]['check_error'])

    async def test_release_identity_counts_even_if_package_bytes_match(self):
        with tempfile.TemporaryDirectory() as root:
            repos=Repositories(root)
            old={'sha256':'a'*64,'tag':'v1','release_id':1}
            repos.state={'repositories':[{'id':'fixture','kind':'release','url':'https://github.com/test/repo','ref':'latest','status':'Ready','actions':[],'logs':[],'commit':old['sha256'],'release':old}]}
            with patch('app.plugins.fetch_release',return_value={**old,'tag':'v2','release_id':2}):await repos.check_update('fixture')
            self.assertTrue(repos.state['repositories'][0]['update_available'])

    async def test_migrates_catalog_and_available_historical_bindings(self):
        with tempfile.TemporaryDirectory() as root:
            repos=Repositories(root)
            record={'repository_id':'fixture','snapshot':'new','commit':'a'*40,'type':'git-fixture-test','manifest':{'version':'2'},'directory':'actions/test'}
            old={**record,'snapshot':'old','commit':'b'*40,'manifest':{'version':'1'}}
            repos.state={'repositories':[{'id':'fixture','url':'https://github.com/test/repo','ref':'main','status':'Ready','actions':[record],'logs':[]}]}
            for name in ('old','new'):(repos.store.root/'sources'/name).mkdir(parents=True)
            Store(root).write('agents.json',{'agents':[{'id':'test','plugin':record,'plugin_history':[old]}]})
            versions=repos.public()['repositories'][0]['versions']
            self.assertEqual({v['snapshot'] for v in versions},{'old','new'})
            await repos.prune_sources()
            self.assertTrue((repos.store.root/'sources'/'old').is_dir())
