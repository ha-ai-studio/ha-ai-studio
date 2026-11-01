"""Disposable IAB fixture. No HA credentials, broker connections or production data."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from aiohttp import web
from app.actions import Studio
from app.action_types import TYPES
from app.plugins import manifest
from app.server import create_app

async def make_app():
    root=Path(tempfile.mkdtemp(prefix='device-ui-'))
    app=create_app(root,dev=True)
    app.cleanup_ctx.clear()  # No MQTT, repository bootstrap or HA startup.
    studio=next(value for value in app.values() if isinstance(value,Studio))
    with patch('app.actions.CATALOG',[TYPES['echo'].metadata]):
        await studio.create({'id':'echo','name':'Echo preview fixture','type':'echo'})
    source=studio.repositories.store.root/'sources'/'ui-fixture'/'actions'/'reader'
    source.mkdir(parents=True)
    (source/'ui').mkdir()
    data={'id':'reader','name':'Synthetic measurement action','description':'Isolated numeric measurements',
          'functions':['execute'],'version':'1','python':f'{sys.version_info.major}.{sys.version_info.minor}',
          'entrypoint':'action.py','editor':'ui/editor.js','settings_schema':{'type':'object'},
          'input_schema':{'type':'object'},'output_schema':{'type':'object'}}
    (source/'action.json').write_text(json.dumps(data))
    (source/'requirements.lock').write_text('')
    (source/'action.py').write_text('def execute(data, settings):\n return {"readings": [{"id": "main", "status": "read", "value": settings["value"], "unit": settings["unit"]}]}\n')
    (source/'ui'/'fields.js').write_text('export const label = "Synthetic editor relative import loaded";')
    (source/'ui'/'editor.js').write_text('import {label} from "./fields.js"; export function render(host, settings) { const p=document.createElement("p"); p.textContent=label; host.append(p); return () => settings; }')
    record={'type':'git-ui-reader','repository_id':'ui','commit':'a'*40,'snapshot':'ui-fixture','directory':'actions/reader','manifest':await manifest(source)}
    studio.repositories.state={'repositories':[{'id':'ui','url':'https://example.invalid','ref':'main','status':'Ready','actions':[record],'logs':[], 'template_snapshot':'ui-fixture', 'device_templates':[{'id':'water-meter','template':{'format':'ha-ai-studio-device-template','version':1,'config':{'name':'Water meter','interval_minutes':30,'publish':False,'sensors':[{'id':'water','name':'Water','device_class':'water','cumulative':True,'unit':'m³','action_id':'reader','reading':'main','max_change':None}], 'functions':[{'id':'read','name':'Read now','action_id':'reader','function':'execute'}]}}}]}]}
    for id,name,value,unit in [('water','Water reader',123.456,'m³'),('temperature','Temperature reader',20.5,'°C')]:
        await studio.create({'id':id,'name':name,'type':record['type']})
        action=studio.get(id);await action.environment.task
        config=action.config['draft'];config['settings']={'value':value,'unit':unit,'meters':[{'id':'main','name':'Main reading'}]}
        await action.save(config,False)
    async def lifecycle(app):
        studio.devices.task=asyncio.create_task(studio.devices.run())
        yield
        await studio.close()
    app.cleanup_ctx.append(lifecycle)
    return app

with patch('app.mqtt.MQTT.connect', side_effect=AssertionError('UI fixture must never connect to MQTT')):
    web.run_app(make_app(),host='0.0.0.0',port=8099,access_log=None)
