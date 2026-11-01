"""Disposable IAB fixture. No HA credentials, broker connections or production data."""
import asyncio
import copy
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
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
    root=Path(os.environ.get('STUDIO_UI_FIXTURE_ROOT') or tempfile.mkdtemp(prefix='device-ui-'))
    app=create_app(root,dev=True)
    app.cleanup_ctx.clear()  # No MQTT, repository bootstrap or HA startup.
    studio=next(value for value in app.values() if isinstance(value,Studio))
    if not studio.services:
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
        leak_source=Path('/fixture-leak/actions/water-leak-detection')
        if leak_source.is_dir():
            destination=source.parent/'water-leak-detection'
            shutil.copytree(leak_source,destination)
            leak_record={**record,'type':'git-ui-leak','directory':'actions/water-leak-detection','manifest':await manifest(destination)}
            studio.repositories.state['repositories'][0]['actions'].append(leak_record)
            await studio.create({'id':'leak','name':'Water leak assessment','type':leak_record['type']})
            action=studio.get('leak');await action.environment.task
            config=action.config['draft'];config['settings']={'entity_id':'sensor.fixture_water','history_hours':24,
                'minimum_rate_lph':1,'minimum_duration_hours':6,'max_gap_minutes':60}
            await action.save(config,False)
            item=studio.devices.create({'name':'Leak test meter'})
            config={**item['draft'],'sensors':[{'id':'leak-status','name':'Leak status',
                'action_id':'leak','reading':'leak-status','device_class':'','unit':'','cumulative':False,'max_change':None}],
                'functions':[{'id':'check-leaks','name':'Check for leaks','action_id':'leak','function':'execute'}]}
            await studio.devices.save(item['id'],{'config':config,'enabled':True})
    # Allow the developer to retain disposable UI state across image rebuilds.
    records={}
    for action in studio.services.values():
        if action.environment:
            record=action.plugin.record
            records[record['type']]=copy.deepcopy(record)
            if record['manifest']['id']=='water-leak-detection':
                # UI-only annotation for old synthetic snapshots; execution bindings stay unchanged.
                action.plugin.metadata['settings_schema']['properties']['entity_id']['x-ha-entity-domains']=['sensor']
                records[record['type']]['manifest']['settings_schema']['properties']['entity_id']['x-ha-entity-domains']=['sensor']
    if records:
        studio.repositories.state={'repositories':[{'id':'ui','url':'https://example.invalid','ref':'main',
            'status':'Ready','actions':list(records.values()),'logs':[],
            'template_snapshot':'ui-fixture','device_templates':studio.repositories.state['repositories'][0].get('device_templates',[])}]}
    async def synthetic_entities(domains,query):
        rows=[{'entity_id':'sensor.fixture_water','name':'Fixture water meter','state':'100.094','available':True}]
        matches=[row for row in rows if row['entity_id'].split('.')[0] in domains
                 and query.casefold() in (row['entity_id']+' '+row['name']).casefold()]
        return {'entities':matches,'total':len(matches)}
    studio.home_assistant.search=synthetic_entities
    async def synthetic_history(method,path,data=None,image=False):
        if method=='GET' and path.startswith('history/period/') and 'filter_entity_id=sensor.fixture_water' in path:
            end=datetime.now(timezone.utc)-timedelta(seconds=1)
            return [[{'entity_id':'sensor.fixture_water','state':str(100+i*.002),
                'last_updated':(end-timedelta(minutes=30*(47-i))).isoformat(),
                'attributes':{'unit_of_measurement':'m³','state_class':'total_increasing'}} for i in range(48)]]
        raise AssertionError('UI fixture must never access real Home Assistant')
    studio.home_assistant.request=synthetic_history
    async def lifecycle(app):
        studio.devices.task=asyncio.create_task(studio.devices.run())
        yield
        await studio.close()
    app.cleanup_ctx.append(lifecycle)
    return app

with patch('app.mqtt.MQTT.connect', side_effect=AssertionError('UI fixture must never connect to MQTT')):
    web.run_app(make_app(),host='0.0.0.0',port=8099,access_log=None)
