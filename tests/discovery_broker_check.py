"""Isolated real MQTT A/B/C checks; not a Home Assistant installation test."""
from unittest.mock import patch
import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import paho.mqtt.client as mqtt
from app.actions import Studio
from app.discovery import DEFAULT
from test_actions import broker
from test_devices import configured_device, sensor, reading


async def main():
    clients=[];seen=[];loop=asyncio.get_running_loop()
    async def observe(host):
        ready=threading.Event()
        client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='observer-'+uuid.uuid4().hex,protocol=mqtt.MQTTv5)
        client.on_connect=lambda c,*_:c.subscribe('#',options=mqtt.SubscribeOptions(qos=1,retainAsPublished=True))
        client.on_subscribe=lambda *_:ready.set()
        client.on_message=lambda c,u,m:loop.call_soon_threadsafe(seen.append,(host,m.topic,m.payload,m.retain))
        await asyncio.to_thread(client.connect,host,1883);client.loop_start()
        assert await asyncio.to_thread(ready.wait,5)
        clients.append(client);return client
    async def until(check):
        for _ in range(400):
            if check():return
            await asyncio.sleep(.025)
        raise AssertionError('Expected MQTT state was not observed')
    async def publish(client,topic,payload,retain=False):
        result=client.publish(topic,payload,qos=1,retain=retain)
        await asyncio.to_thread(result.wait_for_publish,5)
    a=await observe('broker');b=await observe('broker2');c=await observe('broker3')
    with tempfile.TemporaryDirectory() as root:
        studio=Studio(root)
        ids=[]
        for host in ['broker','broker2','broker3']:
            before=set(studio.brokers.all());await studio.brokers.save(broker(host));ids.append((set(studio.brokers.all())-before).pop())
        await studio.create({'id':'echo','name':'Echo','type':'echo'})
        service=studio.get('echo');prefix=service.identity['prefix'];namespace=studio.namespace
        config=copy.deepcopy(service.config['draft']);config['input']={'broker_id':ids[0],'topic':'discovery-check/request'};config['output']={'broker_id':ids[1],'topic':'discovery-check/result'}
        await service.save(config,True)
        await studio.discovery.save({**DEFAULT,'enabled':True,'broker_id':ids[2]})
        device=await configured_device(studio,[sensor(action='echo'),sensor('serial',action='echo',kind='number',unit='')],publish=True)
        prefix=device['identity']
        await studio.start()
        status=f'homeassistant/sensor/{prefix}/status/config';availability=namespace+'/discovery/availability'
        await until(lambda:any(h=='broker3' and t==availability and p==b'online' for h,t,p,r in seen))
        assert not any(h!='broker3' and (t.startswith('homeassistant/') or t==prefix+'/status') for h,t,p,r in seen)
        configs={t:json.loads(p) for h,t,p,r in seen if h=='broker3' and t.endswith('/config') and p}
        assert len(configs)==9
        assert configs[status]['unique_id']==prefix+'_status'
        await until(lambda:service.mqtt and service.mqtt.ready.is_set())
        await publish(a,'discovery-check/request',json.dumps({'request_id':'production','data':{'private':'DO-NOT-ANNOUNCE'}}))
        await until(lambda:service.last and service.last['delivery']=='published')
        assert not any(service.identity['prefix'] in t for h,t,p,r in seen if t.endswith('/config'))
        from unittest.mock import AsyncMock
        with patch.object(service,'execute',AsyncMock(return_value={'readings':[reading(),reading('serial','000123-AB','')]})):
            studio.devices.start_read(device['id']);await studio.devices.tasks[device['id']]
        await until(lambda:any(t==prefix+'/value_water' and p==b'123' for h,t,p,r in seen))
        await until(lambda:any(t==prefix+'/value_serial' and p==b'000123-AB' for h,t,p,r in seen))
        await until(lambda:any(t==prefix+'/status_message_water' and p==b'Reading successful.' for h,t,p,r in seen))
        reason = 'Alignment reference was not found confidently.'
        with patch.object(service,'execute',AsyncMock(return_value={'status':'unreadable','message':reason,'readings':[]})):
            studio.devices.start_read(device['id']);await studio.devices.tasks[device['id']]
        for key in ('water', 'serial'):
            await until(lambda:any(t==prefix+'/status_message_'+key and p==reason.encode() for h,t,p,r in seen))
        recovery_start = len(seen)
        with patch.object(service,'execute',AsyncMock(return_value={'readings':[reading(),reading('serial','000123-AB','')]})):
            studio.devices.start_read(device['id']);await studio.devices.tasks[device['id']]
        await until(lambda:any(t==prefix+'/status_message_water' and p==b'Reading successful.' for h,t,p,r in seen[recovery_start:]))
        serial_config=studio.discovery.desired()[f'homeassistant/sensor/{prefix}/value_serial/config']
        assert 'state_class' not in serial_config and 'unit_of_measurement' not in serial_config
        assert not any(b'DO-NOT-ANNOUNCE' in p for h,t,p,r in seen if h=='broker3')
        before=copy.deepcopy(service.last);await service.test(config,'{"request_id":"ui","data":0}')
        assert before==service.last
        assert any(h=='broker2' and t=='discovery-check/result' and not r for h,t,p,r in seen)
        # Explicit functions share the central discovery transport, never Action brokers.
        with patch.dict(service.plugin.metadata, {'functions':['execute']}):
            function_config=copy.deepcopy(device['draft'])
            function_config['functions']=[{'id':'read-meter','name':'Read meter','action_id':'echo','function':'execute'}]
            device=await studio.devices.save(device['id'],{'config':function_config,'enabled':True})
        command=prefix+'/function/read-meter/request'
        button=f'homeassistant/button/{prefix}/function_read-meter/config'
        await until(lambda:any(t==button and p for h,t,p,r in seen))
        from unittest.mock import AsyncMock
        with patch.object(service,'execute',AsyncMock(return_value={'readings':[reading(),reading('serial','000123-AB','')]})) as execute:
            await publish(a,command,'{"request_id":"wrong-a"}')
            await publish(b,command,'{"request_id":"wrong-b"}')
            await publish(c,command,'{"request_id":"retained"}',True)
            await asyncio.sleep(.2)
            assert execute.await_count==0
            # A reconnect replays the retained request, which must still be rejected.
            await asyncio.to_thread(studio.discovery.client.client._sock_close)
            await asyncio.sleep(2)
            await until(lambda:studio.discovery.client.ready.is_set())
            assert execute.await_count==0
            await publish(c,command,'{"request_id":"button-1"}')
            await until(lambda:execute.await_count==1 and not studio.devices.busy(device['id']))
            await publish(c,command,'{"request_id":"button-1"}')
            await asyncio.sleep(.2)
            assert execute.await_count==1
            assert studio.devices.get(device['id'])['values']['water']['value']==123
            await publish(c,command,'',True) # Clear the test-owned retained command.
        assert not any(h!='broker3' and t==button for h,t,p,r in seen)
        # A retained HA birth is accepted, unlike Action requests; replay both config and state.
        birth_start=len(seen)
        count=sum(t==status for h,t,p,r in seen)
        await publish(c,'homeassistant/status','online',True)
        await until(lambda:sum(t==status for h,t,p,r in seen)>count)
        count=sum(t==status for h,t,p,r in seen)
        await until(lambda:any(t==prefix+'/status' and p==b'ready' for h,t,p,r in seen[birth_start:]))
        reconnect_start=len(seen)
        # Force an actual TCP loss. Paho reconnects and the manager republishes.
        await asyncio.to_thread(studio.discovery.client.client._sock_close)
        await until(lambda:sum(t==status for h,t,p,r in seen)>count)
        await until(lambda:any(t==prefix+'/status' and p==b'ready' for h,t,p,r in seen[reconnect_start:]))
        device_config=copy.deepcopy(device['draft']);device_config['name']='Renamed'
        with patch.dict(service.plugin.metadata, {'functions':['execute']}):
            await studio.devices.save(device['id'],{'config':device_config,'activate':True})
        await until(lambda:any(t==status and b'Renamed' in p for h,t,p,r in seen))
        studio.devices.deactivate(device['id'])
        await until(lambda:command not in studio.discovery.client.commands)
        with patch.object(service,'execute',AsyncMock()) as execute:
            await publish(c,command,'{"request_id":"disabled"}')
            await asyncio.sleep(.2)
            execute.assert_not_awaited()
        await service.deactivate()
        await until(lambda:any(t==prefix+'/status' and p==b'disabled' for h,t,p,r in seen))
        close_start=len(seen)
        await studio.close()
        await until(lambda:any(t==availability and p==b'offline' for h,t,p,r in seen[close_start:]))
        studio=Studio(root);service=studio.get('echo');await studio.start()
        await until(lambda:studio.discovery.status.startswith('Connected'))
        assert studio.discovery.desired()[status]['unique_id']==configs[status]['unique_id']
        assert 'button-1' in studio.devices.get(device['id'])['function_requests']
        studio.devices.delete({'id':device['id'],'confirmed':True})
        await studio.delete({'id':'echo','confirmed':True})
        await until(lambda:any(t==status and p==b'' for h,t,p,r in seen))
        # Switch to B, verify old broker clears only our topics and Studio exists with zero Actions.
        await publish(c,'foreign/keep','untouched',True)
        await studio.discovery.save({**DEFAULT,'enabled':True,'broker_id':ids[1]})
        await until(lambda:studio.discovery.status.startswith('Connected') and all(i['broker_id']==ids[1] for i in studio.discovery.data['owned']))
        assert not any(t=='foreign/keep' and p==b'' for h,t,p,r in seen)
        await studio.discovery.save(dict(DEFAULT))
        await until(lambda:not studio.discovery.data['owned'])
        await studio.close()
        # A fresh observer receives neither deleted configs nor retained results.
        start=len(seen);await observe('broker2');await observe('broker3');await asyncio.sleep(.3)
        assert not any((t.endswith('/config') or t=='discovery-check/result') and p for h,t,p,r in seen[start:])
    # A real TCP proxy lets us make the old isolated broker unreachable without Docker privileges.
    streams=set()
    async def relay(reader,writer):
        upstream,output=await asyncio.open_connection('broker3',1883)
        streams.update((writer,output))
        async def pump(source,destination):
            try:
                while chunk:=await source.read(65536):destination.write(chunk);await destination.drain()
            finally:destination.close()
        try:await asyncio.gather(pump(reader,output),pump(upstream,writer))
        except (OSError,ConnectionError):pass
        finally:streams.discard(writer);streams.discard(output)
    proxy=await asyncio.start_server(relay,'127.0.0.1',28883)
    with tempfile.TemporaryDirectory() as root:
        studio=Studio(root)
        old=(await studio.brokers.save({**broker('127.0.0.1'),'port':28883}))['brokers'][0]['id']
        before=set(studio.brokers.all());await studio.brokers.save(broker('broker2'));new=(set(studio.brokers.all())-before).pop()
        await studio.discovery.save({**DEFAULT,'enabled':True,'broker_id':old})
        await studio.discovery.reconcile();await studio.discovery.disconnect()
        proxy.close();await proxy.wait_closed()
        for writer in list(streams):writer.close()
        await studio.discovery.save({**DEFAULT,'enabled':True,'broker_id':new})
        try:await studio.discovery.reconcile();raise AssertionError('Old broker should be unreachable')
        except OSError:pass
        assert studio.discovery.public()['pending_cleanup']>0
        await studio.close()
        studio=Studio(root)
        assert studio.discovery.public()['pending_cleanup']>0
        proxy=await asyncio.start_server(relay,'127.0.0.1',28883)
        await studio.discovery.reconcile()
        assert studio.discovery.public()['pending_cleanup']==0
        assert all(item['broker_id']==new for item in studio.discovery.data['owned'])
        await studio.discovery.save(dict(DEFAULT));await studio.discovery.reconcile();await studio.close()
    proxy.close();await proxy.wait_closed()
    # Abrupt process death exercises the broker's real LWT, separate from clean close.
    code="""import time
from app.mqtt import MQTT
c=MQTT({'prefix':'isolated-will','device_id':'will','availability_topic':'isolated/will'},lambda _:None,subscribe=False)
c.connect({'mqtt':{'mode':'manual','host':'broker3','port':1883}})
c.publish('isolated/will','online',absolute=True)
print('ready',flush=True)
time.sleep(60)
"""
    child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,text=True)
    try:
        assert await asyncio.to_thread(child.stdout.readline)=='ready\n'
        child.kill();await asyncio.to_thread(child.wait)
        await until(lambda:any(t=='isolated/will' and p==b'offline' for h,t,p,r in seen))
    finally:
        if child.poll() is None:child.kill();child.wait()
    for client in clients:client.disconnect();await asyncio.to_thread(client.loop_stop)
    print('PASS: real isolated A/B/C; central discovery; stable IDs; HA birth; TCP reconnect; restart; clean offline and hard-kill LWT; rename/deactivate/delete; broker switch/off; UI test isolation; private payload exclusion; no retained results; foreign topics preserved.')

from app.action_types import TYPES
# Exercise historical instances without exposing the compatibility type in production.
with patch("app.actions.CATALOG",[TYPES["echo"].metadata]), patch("app.plugins.Repositories.start"):
    asyncio.run(main())
