"""Disposable version-management UI fixture. No MQTT or Studio lifecycle starts."""
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from aiohttp import web
from app.actions import Studio
from app.server import create_app
from app.releases import ASSET
from test_plugins_release import package

async def main():
    app=create_app(tempfile.mkdtemp(prefix='studio-versions-'),dev=True)
    app.cleanup_ctx.clear()
    studio=next(v for v in app.values() if isinstance(v,Studio))
    version='1'
    packages={v:package(v) for v in ('1','2','3')}
    async def download(session,url,limit):
        data=packages[version]
        if url.endswith('.sha256'):return (hashlib.sha256(data).hexdigest()+'  '+ASSET+'\n').encode()
        if url.endswith('.zip'):return data
        return json.dumps({'id':int(version),'tag_name':'v'+version,'draft':False,'prerelease':False,
            'assets':[{'name':name,'state':'uploaded','browser_download_url':'https://github.com/test/repo/releases/download/v'+version+'/'+name} for name in (ASSET,ASSET+'.sha256')]}).encode()
    mock=patch('app.releases.download',side_effect=download);mock.start()
    repos=studio.repositories
    result=await repos.save({'kind':'release','url':'https://github.com/test/repo','ref':'latest'})
    ident=result['repositories'][0]['id'];await repos.tasks[ident]
    kind=next(iter(repos.types()))
    await studio.create({'id':'version-fixture','name':'Version fixture','type':kind})
    await studio.get('version-fixture').environment.task
    version='2';repos.refresh(ident);await repos.tasks[ident]
    version='3';await repos.check_update(ident)
    async def cleanup(app):
        mock.stop();await studio.close()
    app.on_cleanup.append(cleanup)
    outer=web.Application();outer.add_subapp('/api/hassio_ingress/version-check',app)
    return outer

if __name__=='__main__':web.run_app(main(),host='0.0.0.0',port=8099,access_log=None)
