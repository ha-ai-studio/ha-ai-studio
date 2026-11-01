"""Real MQTT checks on two isolated brokers. No personal credentials or HA access."""
import asyncio
import copy
import shutil
import json
import tempfile
import threading
import time
import uuid
import sys
from pathlib import Path
from app.plugins import manifest
from app.mqtt import MQTT
from unittest.mock import patch
import paho.mqtt.client as mqtt
from app.actions import Studio
from test_actions import broker

async def main():
    loop=asyncio.get_running_loop();clients=[];events=asyncio.Queue();seen=[]
    async def observe(host):
        ready=threading.Event();client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='test-'+uuid.uuid4().hex)
        client.on_connect=lambda c,*args:c.subscribe('#',qos=1)
        client.on_subscribe=lambda *args:ready.set()
        def receive(c,u,msg):
            try:value=json.loads(msg.payload)
            except ValueError:return
            event=(host,msg.topic,value,msg.retain)
            loop.call_soon_threadsafe(events.put_nowait,event);loop.call_soon_threadsafe(seen.append,event)
        client.on_message=receive
        await asyncio.to_thread(client.connect,host,1883);client.loop_start();assert await asyncio.to_thread(ready.wait,5)
        clients.append(client);return client
    async def send(client,topic,body,retain=False,raw=False):
        info=client.publish(topic,body if raw else json.dumps(body),qos=1,retain=retain);await asyncio.to_thread(info.wait_for_publish,5)
    async def response(id):
        while True:
            host,topic,body,retained=await asyncio.wait_for(events.get(),15)
            if host=='broker2' and topic=='echo/result' and body.get('request_id')==id:return body
    async def idle(service):
        for _ in range(500):
            if not service.busy:return
            await asyncio.sleep(.01)
        raise AssertionError('Service remained busy')
    async def connected(service):
        # Studio.run owns connection setup. Calling publish here would race its
        # startup task and create two clients with the same MQTT client ID.
        try:
            async with asyncio.timeout(15):
                while service.mqtt is None or not service.mqtt.ready.is_set():
                    if service.task and service.task.done():
                        raise AssertionError('Action background task stopped before MQTT became ready')
                    await asyncio.sleep(.01)
        except TimeoutError:
            raise AssertionError('Action subscription did not become ready: '+service.mqtt_status) from None
    a_client=await observe('broker');b_client=await observe('broker2')
    with tempfile.TemporaryDirectory() as root:
        studio=Studio(root)
        a=(await studio.brokers.save(broker()))['brokers'][0]['id'];before=set(studio.brokers.all());await studio.brokers.save(broker('broker2'));b=next(iter(set(studio.brokers.all())-before))
        await studio.create({'id':'echo','name':'Echo','type':'echo'});service=studio.get('echo');config=service.config['draft']
        config['input']={'broker_id':a,'topic':'echo/request'};config['output']={'broker_id':b,'topic':'echo/result'}
        await service.save(config,False);await service.publish();assert service.mqtt is None
        await service.test(config,'{"request_id":"test-only","data":0}');assert not service.jobs
        await service.save(config,True);await studio.start();await connected(service);await idle(service)
        await send(a_client,'echo/request',{'request_id':'live-retained','data':0},True)
        await asyncio.sleep(.2);assert 'live-retained' not in service.jobs
        for n,data in enumerate([None,False,0,[],[1,False],{'message':'Hello','value':42}]):
            request={'request_id':str(n),'data':data};await send(a_client,'echo/request',request)
            assert await response(str(n))=={'request_id':str(n),'status':'success','data':data}
        await idle(service)
        await send(a_client,'echo/request',{'request_id':'5','data':{'message':'Hello','value':42}});assert (await response('5'))['status']=='success'
        await send(a_client,'echo/request',{'request_id':'5','data':'different'});assert (await response('5'))['error']['code']=='request_id_conflict'
        await send(a_client,'echo/request','{',raw=True);assert (await response(None))['error']['code']=='invalid_request'
        await send(a_client,'echo/request',{'request_id':'invalid'});assert (await response('invalid'))['error']['code']=='invalid_request'
        await idle(service);await studio.close()
        await send(a_client,'echo/request',{'request_id':'retained','data':1},True)
        studio=Studio(root);service=studio.get('echo');await studio.start();await connected(service);await idle(service)
        await send(a_client,'echo/request',{'request_id':'5','data':{'message':'Hello','value':42}});assert (await response('5'))['status']=='success'
        await idle(service);assert 'retained' not in service.jobs
        # Actual connection failure, followed by durable cached-result retry to original broker.
        with patch('app.actions.MQTT.connect',side_effect=OSError('isolated outage')):
            await service.run_request_payload('{"request_id":"outage","data":false}')
        assert service.last['execution']=='success' and service.last['delivery']=='failed'
        await send(a_client,'echo/request',{'request_id':'outage','data':False});assert (await response('outage'))['data'] is False
        await idle(service);assert service.last['delivery']=='published'
        await service.deactivate();await send(a_client,'echo/request',{'request_id':'inactive','data':0});await asyncio.sleep(.2);assert 'inactive' not in service.jobs
        await service.save(config,True);await connected(service);await send(a_client,'echo/request',{'request_id':'reactivated','data':42});assert (await response('reactivated'))['data']==42
        await idle(service);await studio.delete({'id':'echo','confirmed':True});await send(a_client,'echo/request',{'request_id':'deleted','data':0});await asyncio.sleep(.1);assert 'deleted' not in service.jobs
        # A real dynamically installed Git type follows the same MQTT durability path.
        dest=studio.repositories.store.root/'sources'/'synthetic'/'actions'/'sample';dest.mkdir(parents=True)
        (dest/'action.py').write_text('async def execute(data, settings):\n return data\n')
        (dest/'requirements.lock').write_text('')
        (dest/'action.json').write_text(json.dumps({'id':'sample','name':'Synthetic','description':'Isolated MQTT fixture','version':'1','python':f'{sys.version_info.major}.{sys.version_info.minor}','entrypoint':'action.py','settings_schema':{'type':'object'}}))
        record={'type':'git-test-sample','repository_id':'test','commit':'a'*40,'snapshot':'synthetic','directory':'actions/sample','manifest':await manifest(dest)}
        catalog={'repositories':[{'id':'test','url':'https://example.invalid/test.git','ref':'main','status':'Ready','actions':[record],'logs':[]}]}
        studio.repositories.state=catalog;studio.repositories.store.write('catalog.json',catalog)
        await studio.create({'id':'dynamic','name':'Dynamic','type':record['type']});dynamic=studio.get('dynamic');await dynamic.environment.task
        assert dynamic.environment.ready,dynamic.environment.state
        conf=dynamic.config['draft'];conf['input']={'broker_id':a,'topic':'echo/request'};conf['output']={'broker_id':b,'topic':'echo/result'}
        await dynamic.save(conf,True);await connected(dynamic)
        for n,data in enumerate([None,False,0,'text',[],{'x':[1,True]}]):
            ident='git-'+str(n);await send(a_client,'echo/request',{'request_id':ident,'data':data});assert (await response(ident))['data']==data
        await idle(dynamic)
        await send(a_client,'echo/request',{'request_id':'git-5','data':{'x':[1,True]}});assert (await response('git-5'))['status']=='success'
        await send(a_client,'echo/request',{'request_id':'git-5','data':False});assert (await response('git-5'))['error']['code']=='request_id_conflict'
        await send(a_client,'echo/request',{'request_id':'git-retained','data':0},True);await asyncio.sleep(.2);assert 'git-retained' not in dynamic.jobs
        await idle(dynamic)
        # A live MQTT subscription must keep serving the old version while a separate
        # venv is prepared, then serve the new version without reconnecting.
        from app.plugins import Environment
        upgraded=studio.repositories.store.root/'sources'/'synthetic-v2'
        shutil.copytree(dest.parent.parent,upgraded)
        (upgraded/'actions/sample/action.py').write_text('async def execute(data, settings):\n return {"version":2,"value":data}\n')
        newer=copy.deepcopy(record);newer.update(snapshot='synthetic-v2',commit='b'*40)
        newer['manifest']['version']='2'
        catalog['repositories'][0]['versions']=[{'snapshot':'synthetic-v2','commit':'b'*40,'actions':[newer]}]
        studio.repositories.store.write('catalog.json',catalog)
        current_client=dynamic.mqtt;old_path=dynamic.environment.path
        started=asyncio.Event();release=asyncio.Event();install=Environment.install
        async def prepare(environment):
            started.set();await release.wait();await install(environment)
        with patch.object(Environment,'install',prepare):
            update=asyncio.create_task(studio.change_version('dynamic',{'snapshot':'synthetic-v2'}))
            await asyncio.wait_for(started.wait(),5)
            await send(a_client,'echo/request',{'request_id':'during-update','data':42})
            assert (await response('during-update'))['data']==42
            release.set();await asyncio.wait_for(update,60)
        assert dynamic.enabled and dynamic.mqtt is current_client
        assert not old_path.exists()
        await send(a_client,'echo/request',{'request_id':'after-update','data':42})
        assert (await response('after-update'))['data']=={'version':2,'value':42}
        await send(a_client,'echo/request',{'request_id':'during-update','data':42})
        assert (await response('during-update'))['data']==42
        await idle(dynamic);await studio.close();studio=Studio(root);dynamic=studio.get('dynamic')
        # Widen the startup window that used to let the test and run() connect
        # the same MQTT client ID concurrently. Only the background task may connect.
        original_connect=MQTT.connect;input_connections=[]
        def delayed_connect(client, settings):
            if client.subscribe:
                input_connections.append(client)
                time.sleep(.1)
            return original_connect(client,settings)
        with patch('app.actions.MQTT.connect',delayed_connect):
            await studio.start();await connected(dynamic)
        assert len(input_connections)==1,'Restart must establish exactly one input connection'

        await send(a_client,'echo/request',{'request_id':'git-5','data':{'x':[1,True]}});assert (await response('git-5'))['status']=='success'
        await idle(dynamic);await dynamic.deactivate();await studio.close()
        assert not any(host=='broker' and topic=='echo/result' for host,topic,_,_ in seen)
        assert not any('/internal/' in topic for _,topic,_,_ in seen)
        before=len(seen);await observe('broker');await observe('broker2');await asyncio.sleep(.3)
        assert not any(topic=='echo/result' for _,topic,_,_ in seen[before:]),'Results must not be retained'
    for client in clients:client.disconnect();await asyncio.to_thread(client.loop_stop)
    print('PASS: live MQTT hot version update without reconnect; old/new execution and cached results; Git action venv + async runner across brokers, restart, deduplication/conflict and retained rejection; Echo across brokers A/B; all JSON types; duplicate/conflict; invalid JSON; retained rejection; restart; delivery failure/retry; draft test isolation; deactivate/reactivate/delete; no internal workflow topics or retained results.')

from app.action_types import TYPES
# Exercise historical instances without exposing the compatibility type in production.
with patch("app.actions.CATALOG",[TYPES["echo"].metadata]), patch("app.plugins.Repositories.start"):
    asyncio.run(main())
