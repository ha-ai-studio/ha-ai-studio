"""Configured Actions shared by MQTT requests and virtual Device measurements."""
import asyncio
import copy
import hashlib
import json
import os
import re
import uuid
from collections import deque
from .domain import Invalid, stamp
from .store import Store
from .brokers import Brokers, topic_check
from .mqtt import MQTT, run_transport
from .action_types import TYPES, CATALOG
from .plugins import Repositories, Environment, GitPlugin

MAX_BYTES=16384
MAX_JOBS=256

def error_result(request_id,code,message):
    return {'request_id':request_id,'status':'error','error':{'code':code,'message':message}}

def valid_id(value):
    return isinstance(value,str) and 0<len(value)<=128 and bool(value.strip()) and not any(ord(c)<32 for c in value)

def no_duplicates(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON key')
        result[key]=value
    return result

async def parse(payload,plugin):
    request_id=None
    try:
        body=json.loads(payload,object_pairs_hook=no_duplicates,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        if isinstance(body,dict) and valid_id(body.get('request_id')):request_id=body['request_id']
        if len(payload.encode() if isinstance(payload,str) else payload)>MAX_BYTES:
            return None,error_result(request_id,'message_too_large','Request exceeds 16384 bytes.')
        if request_id is None:return None,error_result(None,'invalid_request_id','request_id must be a non-empty string of up to 128 characters without control characters.')
        if isinstance(plugin,GitPlugin):await plugin.validate_input(body)
        else:plugin.validate_input(body)
        # Reject overflow to infinity, invalid surrogates and non-finite numbers.
        json.dumps(body,allow_nan=False,ensure_ascii=False).encode('utf-8')
        return body,None
    except (ValueError,TypeError,UnicodeError,RecursionError):
        return None,error_result(request_id,'invalid_request','Provide valid finite JSON with request_id and data; duplicate keys and additional fields are not allowed.')

def digest(body):
    return hashlib.sha256(json.dumps(body,sort_keys=True,allow_nan=False,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

class ActionService:
    def __init__(self,root,definition,namespace,brokers,repositories=None):
        self.store=Store(root);self.definition=copy.deepcopy(definition);self.brokers=brokers
        self.type=definition.get('action_type')
        self.plugin=repositories.bound(definition['plugin']) if definition.get('plugin') and repositories else TYPES.get(self.type)
        self.supported=self.plugin is not None
        self.environment=Environment(self.store,self.plugin) if definition.get('plugin') and self.plugin else None
        if self.environment:self.environment.home_assistant=getattr(repositories,'home_assistant',None)
        from .assets import ActionAssets
        self.assets=ActionAssets(self)
        self.device_configs=lambda: []
        self.identity=self.store.read('identity.json')
        if self.identity is None:
            ident='studio_'+hashlib.sha256((namespace+'/'+definition['id']).encode()).hexdigest()[:24];self.identity={'prefix':ident,'device_id':ident};self.store.write('identity.json',self.identity)
        self.identity.update(command_prefix=f'ha-ai-studio/{namespace}/agents/{definition["id"]}',name=definition['name'])
        self.config=self.store.read('action-config.json') if self.supported else None
        if self.supported and self.config is None:
            self.config={'draft':{'name':definition['name'],'input':{'broker_id':None,'topic':self.identity['command_prefix']+'/request'},
                'settings':{},'output':{'broker_id':None,'topic':self.identity['command_prefix']+'/result'}},'active':None,'enabled':False}
            self.store.write('action-config.json',self.config)
        self.jobs=self.store.read('action-jobs.json',{}) if self.supported else {}
        for id,job in self.jobs.items():
            if job.get('execution')=='processing':job['execution']='interrupted'
            if job.get('delivery')=='pending':
                job['delivery']='unknown'
        if self.jobs:self.store.write('action-jobs.json',self.jobs)
        self.last=self.store.read('action-last.json') if self.supported else None
        if self.last and self.last.get('delivery')=='pending':
            self.last['delivery']='unknown';self.store.write('action-last.json',self.last)
        if self.last and self.last['execution']=='processing':
            self.last.update(execution='interrupted',delivery='not_sent');self.store.write('action-last.json',self.last)
        self.events=deque(maxlen=200);self.event_sequence=0;self.event_epoch=uuid.uuid4().hex
        self.lock=asyncio.Lock();self.mqtt=None;self.task=None;self.job=None;self.deleted=False
        self.delivering=False;self.running=False;self.transport_generation=0;self.queue=asyncio.Queue(maxsize=16)
        self.mqtt_status='Not connected' if self.enabled else 'Inactive'

    def trace(self,kind,message,payload=None,topic=None):
        self.event_sequence+=1
        entry={'id':self.event_sequence,'timestamp':stamp(),'kind':kind,'message':message}
        if topic is not None:entry['topic']=topic
        if payload is not None:
            text=payload.decode('utf-8',errors='replace') if isinstance(payload,bytes) else payload if isinstance(payload,str) else json.dumps(payload,ensure_ascii=True)
            entry['payload']=text[:20000];entry['truncated']=len(text)>20000
        self.events.append(entry)
    def live(self,after=0):
        return {'epoch':self.event_epoch,'cursor':self.event_sequence,'mqtt':self.mqtt_status,
                'events':[copy.deepcopy(e) for e in self.events if e['id']>after],
                'gap':bool(self.events and after and after<self.events[0]['id']-1)}

    @property
    def enabled(self):return self.supported and self.config['enabled'] and bool(self.config['active']) and not self.deleted
    @property
    def busy(self):return bool(self.environment and self.environment.task and not self.environment.task.done()) or self.lock.locked() or self.running or self.delivering or bool(self.job and not self.job.done())
    def ensure_idle(self):
        if self.deleted:raise Invalid('Action has been deleted.')
        if self.busy:raise Invalid('Action is busy. Try again after the current operation.')
    async def check_config(self,value):
        if not self.supported:raise Invalid('Unsupported legacy action. Its original files are preserved; create a new Echo action.')
        if not isinstance(value,dict) or set(value)!={'name','input','settings','output'}:raise Invalid('Provide name, input, settings and output.')
        if not isinstance(value['name'],str) or not 1<=len(value['name'].strip())<=80 or any(ord(c)<32 for c in value['name']):raise Invalid('Name must contain 1 to 80 characters.')
        for key in ('input','output'):
            part=value[key]
            if not isinstance(part,dict) or set(part)!={'broker_id','topic'}:raise Invalid('Provide broker and topic for input and output.')
            if part['broker_id'] is not None and (not isinstance(part['broker_id'],str) or len(part['broker_id'])>80):raise Invalid('Invalid broker selection.')
            topic_check(part['topic'])
        if self.environment:await self.plugin.validate(value['settings'])
        else:self.plugin.validate(value['settings'])
        result=copy.deepcopy(value);result['name']=result['name'].strip();return result
    def routes(self,config=None):
        value=config or (self.config or {}).get('active')
        if not value:raise Invalid('Activate an action configuration first.')
        return {'input_broker':value['input']['broker_id'],'output_broker':value['output']['broker_id'] or value['input']['broker_id'],
            'request_topic':value['input']['topic'],'result_topic':value['output']['topic']}
    def references_broker(self,id):
        values=[]
        if self.supported:
            values=[self.config['draft'],self.config['active'],*(j['config'] for j in self.jobs.values())]
            return any(v and id in (self.routes(v)['input_broker'],self.routes(v)['output_broker']) for v in values)
        old=self.store.read('workflow.json',{})
        return any(w and any(s.get('config',{}).get('broker_id')==id for s in w.get('steps',[])) for w in old.values())
    def check_routes(self,config):
        route=self.routes(config)
        self.brokers.get(route['input_broker']);self.brokers.get(route['output_broker'])
        self.brokers.check_topology(self,config)
    def status(self):
        issues=[]
        if self.environment and not self.environment.ready:issues.append('Execution blocked: Python environment is not ready. Deactivate and rebuild it.')
        gateway=getattr(self.environment,'home_assistant',None) if self.environment else None
        if gateway:
            from .home_assistant import configured_entities
            configured=configured_entities(self.config['draft']['settings']) | configured_entities((self.config.get('active') or {}).get('settings',{}))
            if configured & set(gateway.pending):issues.append('Illumination restoration is pending. Studio will retry; check the light. New captures are blocked until restoration succeeds.')
        if self.enabled:
            try:self.check_routes(self.config['active'])
            except Invalid as exc:issues.append(str(exc))
        return {'definition':{'id':self.definition['id'],'name':self.config['draft']['name'] if self.supported else self.definition['name'],'type':self.type},
            'supported':self.supported,'config':copy.deepcopy(self.config),'enabled':self.enabled,'busy':self.busy,'mqtt':self.mqtt_status,
            'environment':self.environment.public() if self.environment else {'status':'Ready','logs':['Built-in action.']},
            'issues':issues if self.supported else ['Unsupported legacy workflow. Disabled; original files and IDs are preserved.'],
            'last':copy.deepcopy(self.last),'plugin':self.plugin.metadata if self.supported else None}
    async def save(self,value,activate,*,set_enabled=None):
        if type(activate) is not bool:raise Invalid('Active must be true or false.')
        if set_enabled is not None and (type(set_enabled) is not bool or set_enabled!=activate):raise Invalid('Invalid Active state.')
        async with self.brokers.lock:
            self.ensure_idle();value=await self.check_config(value)
            self.ensure_idle()
            if activate:
                if self.environment:self.environment.require()
                self.check_routes(value)
            candidate={'draft':value,'active':copy.deepcopy(value) if activate else self.config['active'],'enabled':True if activate else self.config['enabled']}
            if set_enabled is not None:candidate['enabled']=set_enabled
            async with self.lock:
                self.store.write('action-config.json',candidate);self.config=candidate
                if activate or set_enabled is False:await self.disconnect()
        return self.status()
    async def deactivate(self):
        async with self.brokers.lock:
            self.ensure_idle()
            if not self.supported:return self.status()
            async with self.lock:
                candidate={**self.config,'enabled':False};self.store.write('action-config.json',candidate);self.config=candidate
                await self.disconnect()
        return self.status()
    async def test(self,config,payload):
        if self.environment:raise Invalid('Draft preview is unavailable for Git actions. Use an isolated MQTT test instance.')
        config=await self.check_config(config);body,error=await parse(payload,self.plugin)
        if error:return {'mode':'test','delivery':'not_sent','result':error}
        try:
            data=await self.execute(body['data'],config['settings'])
            result={'request_id':body['request_id'],'status':'success','data':data}
        except Exception:result=error_result(body['request_id'],'execution_failed','Action execution failed.')
        return {'mode':'test','delivery':'not_sent','result':result}
    async def execute(self,data,settings):
        if self.environment:return await self.environment.execute(data,settings)
        return await asyncio.wait_for(self.plugin.execute(data,settings),30)
    async def request(self,payload,configuration=None):
        # The caller owns lock throughout execution and delivery.
        body,error=await parse(payload,self.plugin)
        if error:
            self.trace('error',error['error']['message']);return error,self.config['active']
        id=body['request_id'];hashed=digest(body);cached=self.jobs.get(id)
        if cached:
            self.trace('processing','Existing request ID: checking cached result and payload digest.')
            if cached['digest']!=hashed:return error_result(id,'request_id_conflict','This request_id was already used for different data.'),self.config['active']
            if cached.get('result') is not None:return cached['result'],cached['config']
            return error_result(id,'interrupted','Execution was interrupted. Use a new request_id only to intentionally start another execution.'),cached['config']
        config=copy.deepcopy(configuration if configuration is not None else self.config['active']);entry={'digest':hashed,'config':config,'result':None,'execution':'processing','delivery':'not_sent'}
        if self.environment:entry['plugin']=copy.deepcopy(self.plugin.record)
        if configuration is not None:entry['source']='device'
        candidate={**self.jobs,id:entry};candidate=dict(list(candidate.items())[-MAX_JOBS:])
        self.store.write('action-jobs.json',candidate);self.jobs=candidate
        self.last={'request_id':id,'execution':'processing','delivery':'not_sent','timestamp':stamp()};self.store.write('action-last.json',self.last)
        self.trace('processing','Validated request; executing '+self.type+'.')
        self.running=True
        try:
            data=await self.execute(body['data'],config['settings'])
            json.dumps(data,allow_nan=False)
            result={'request_id':id,'status':'success','data':data}
        except asyncio.CancelledError:raise
        except Exception:result=error_result(id,'execution_failed','Action execution failed or timed out.')
        finally:self.running=False
        candidate={**self.jobs,id:{**entry,'result':result,'execution':result['status']}}
        self.store.write('action-jobs.json',candidate);self.jobs=candidate
        self.trace('processing','Execution finished: '+result['status']+'.')
        return result,config
    async def internal_request(self, request_id, data, config):
        """Share the persisted execution path without MQTT input or delivery."""
        async with self.lock:
            if self.deleted or not self.supported:raise Invalid('Action is unavailable.')
            if self.environment:self.environment.require()
            result, _ = await self.request(json.dumps({'request_id':request_id,'data':data}), config)
            self.last={'request_id':request_id,'execution':result['status'],'delivery':'not_sent','timestamp':stamp(),'source':'device'}
            self.store.write('action-last.json',self.last)
            return result

    async def run_request_payload(self,payload,generation=None):
        async with self.lock:
            if generation is not None and generation!=self.transport_generation:return None
            if not self.enabled:return error_result(None,'inactive','Action is inactive.')
            self.trace('input','Incoming MQTT request',payload,self.routes()['request_topic'])
            result,config=await self.request(payload)
            self.last={'request_id':result['request_id'],'execution':'interrupted' if result.get('error',{}).get('code')=='interrupted' else result['status'],'delivery':'pending','timestamp':stamp()}
            self.store.write('action-last.json',self.last)
            cached=self.jobs.get(result['request_id'])
            owned=cached is not None and cached.get('result')==result
            if owned:
                candidate={**self.jobs,result['request_id']:{**cached,'delivery':'pending'}}
                self.store.write('action-jobs.json',candidate);self.jobs=candidate
            self.delivering=True;delivery='failed'
            client=MQTT({'prefix':self.identity['prefix']+'_out_'+uuid.uuid4().hex[:8],'device_id':self.identity['device_id']},lambda _:None,subscribe=False)
            try:
                route=self.routes(config)
                self.trace('output','Publishing MQTT response',result,route['result_topic'])
                await run_transport(client.connect,self.brokers.get(route['output_broker'])['settings'])
                await run_transport(client.publish,route['result_topic'],result,absolute=True,retain=False)
                delivery='published'
                self.trace('delivery','Response acknowledged by the MQTT broker.')
            except asyncio.CancelledError:
                delivery='unknown'
                self.trace('delivery','Transport interrupted during shutdown; delivery is unknown.')
                raise
            except Exception:
                self.mqtt_status='Output delivery failed. Retry the identical request to resend the saved result.'
                self.trace('error',self.mqtt_status)
            finally:
                try:await run_transport(client.close)
                finally:
                    self.delivering=False;self.last['delivery']=delivery
                    self.store.write('action-last.json',self.last)
                    if owned:
                        candidate={**self.jobs,result['request_id']:{**self.jobs[result['request_id']],'delivery':delivery}}
                        self.store.write('action-jobs.json',candidate);self.jobs=candidate
            return result
    async def disconnect(self):
        self.transport_generation+=1
        if self.mqtt:
            client=self.mqtt;self.mqtt=None;await run_transport(client.close)
        while not self.queue.empty():self.queue.get_nowait();self.queue.task_done()
        self.mqtt_status='Not connected' if self.enabled else 'Inactive'
    async def publish(self):
        if not self.enabled:return
        if self.environment:self.environment.require()
        self.check_routes(self.config['active']);route=self.routes()
        if not self.mqtt:
            loop=asyncio.get_running_loop();generation=self.transport_generation
            client=MQTT({**self.identity,'request_topic':route['request_topic']},lambda data:loop.call_soon_threadsafe(self.trigger,data,generation))
            try:await run_transport(client.connect,self.brokers.get(route['input_broker'])['settings'])
            except BaseException:await run_transport(client.close);raise
            self.mqtt=client
        self.mqtt_status='Connected' if self.mqtt.ready.is_set() else 'Reconnecting'
    def trigger(self,payload,generation=None):
        if not self.enabled or (generation is not None and generation!=self.transport_generation):return
        try:
            body=json.loads(payload)
            if isinstance(body,dict) and 'status' in body:
                self.trace('ignored','Ignored result envelope on the input topic.');return # Defense against loops, including DNS aliases / external bridges.
        except (ValueError,TypeError,UnicodeError,RecursionError):pass
        if not self.queue.full():self.queue.put_nowait((self.transport_generation,payload))
        else:self.trace('error','Incoming request dropped: queue is full.')
    async def run(self):
        while True:
            try:
                async with self.lock:await self.publish()
            except Exception:self.mqtt_status='Execution blocked: Python environment is not ready.' if self.environment and not self.environment.ready else 'Connection or subscription failed. Check broker settings and topic permissions.'
            try:generation,payload=await asyncio.wait_for(self.queue.get(),3)
            except asyncio.TimeoutError:continue
            try:await self.run_request_payload(payload,generation)
            except Exception:
                self.mqtt_status='Request could not be completed. Check storage and broker access; retry the same ID.'
                self.trace('error',self.mqtt_status)
            finally:self.queue.task_done()
    async def close(self):
        if self.environment:await self.environment.close()
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
        await self.disconnect()

class Studio:
    def __init__(self,root):
        self.store=Store(root);self.lock=asyncio.Lock();self.running=False
        self.repositories=Repositories(root)
        from .home_assistant import HomeAssistant
        self.home_assistant=HomeAssistant(root);self.repositories.home_assistant=self.home_assistant
        # Preserve the registry, paths and protocol namespace across the public rename.
        self.registry=self.store.read('agents.json',{'agents':[],'legacy_id':None})
        identity=self.store.read('identity.json')
        if identity is None:
            ident='studio_'+uuid.uuid4().hex[:16];identity={'prefix':ident,'device_id':ident};self.store.write('identity.json',identity)
        # Adopt existing namespaces without changing Discovery identities or routes.
        if 'instance_id' not in identity:
            identity['instance_id']=identity['prefix']
            self.store.write('identity.json',identity)
        self.instance_id=identity['instance_id']
        self.namespace=identity['prefix'];self.services={};self.brokers=Brokers(self.store);self.brokers.services=lambda:list(self.services.values())
        for config in self.registry['agents']:
            location=self.store.root if config['id']==self.registry.get('legacy_id') else self.store.root/'agents'/config['id']
            self.services[config['id']]=ActionService(location,config,self.namespace,self.brokers,self.repositories)
        from .devices import Devices
        self.devices=Devices(self)
        for id, service in self.services.items():service.device_configs=lambda id=id: self.devices.action_configs(id)
        from .discovery import Discovery
        self.discovery=Discovery(self)
        self.brokers.discovery=self.discovery
    def get(self,id):
        if not isinstance(id,str) or id not in self.services:raise Invalid('Action not found.')
        return self.services[id]
    def list(self):return {'actions':[s.status() for s in self.services.values()],'types':CATALOG+[p.metadata for p in self.repositories.types().values()],'dev':os.getenv('HA_AI_STUDIO_DEV')=='1'}
    async def create(self,value):
        if not isinstance(value,dict) or set(value)!={'id','name','type'}:raise Invalid('Provide id, name and type.')
        if not isinstance(value['id'],str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}',value['id']):raise Invalid('Action ID must use lowercase letters, digits and hyphens, up to 48 characters.')
        if not isinstance(value['name'],str) or not 1<=len(value['name'].strip())<=80 or any(ord(c)<32 for c in value['name']):raise Invalid('Name must contain 1 to 80 characters.')
        if not isinstance(value['type'],str) or value['type'] not in {p['type'] for p in CATALOG} | set(self.repositories.types()):raise Invalid('Select an action type from a configured Git repository.')
        async with self.lock:
            id=value['id']
            if id in self.services or any(c['id']==id for c in self.registry.get('deleted',[])):raise Invalid('Action ID is in use or reserved.')
            if len(self.services)>=32:raise Invalid('Maximum of 32 actions.')
            definition={'id':id,'name':value['name'].strip(),'action_type':value['type']}
            if value['type'] not in TYPES:definition['plugin']=self.repositories.types()[value['type']].record
            service=ActionService(self.store.root/'agents'/id,definition,self.namespace,self.brokers,self.repositories)
            service.device_configs=lambda: self.devices.action_configs(id)
            candidate=copy.deepcopy(self.registry);candidate['agents'].append(definition)
            self.store.write('agents.json',candidate);self.registry=candidate;self.services[id]=service
            if self.running:service.task=asyncio.create_task(service.run())
            if service.environment:service.environment.start()
            return service.status()
    async def reload_source(self,id):
        async with self.brokers.lock:
            async with self.lock:
                service=self.get(id);service.ensure_idle()
                if self.devices.references(id, active_only=True):raise Invalid('Deactivate devices using this action before reloading its source.')
                if service.enabled:raise Invalid('Deactivate the action before reloading its source.')
                if not service.environment or not service.plugin.metadata.get('local'):raise Invalid('Only local actions support source reload.')
                async with service.lock:
                    repository_id=service.plugin.record['repository_id']
                    self.repositories.refresh(repository_id)
                    await self.repositories.tasks[repository_id]
                    repo=next(r for r in self.repositories.state['repositories'] if r['id']==repository_id)
                    if repo['status']!='Ready':raise Invalid('Local source refresh failed. The previous action is preserved.')
                    plugin=self.repositories.types().get(service.type)
                    if plugin is None:raise Invalid('The action ID is missing from the local source.')
                    snapshot=plugin.record['snapshot'];self.repositories.active_snapshots.add(snapshot)
                    try:
                        await plugin.validate(service.config['draft']['settings'])
                        candidate=copy.deepcopy(self.registry)
                        definition=next(d for d in candidate['agents'] if d['id']==id)
                        history=definition.setdefault('plugin_history',[])
                        if not any(r['snapshot']==service.plugin.record['snapshot'] for r in history):history.append(copy.deepcopy(service.plugin.record))
                        definition['plugin']=copy.deepcopy(plugin.record)
                        self.store.write('agents.json',candidate);self.registry=candidate
                        service.definition=copy.deepcopy(definition);service.plugin=plugin
                        service.environment.plugin=plugin
                        service.environment.start()
                    finally:self.repositories.active_snapshots.discard(snapshot)
                return service.status()
    async def delete_repository(self,value):
        # Serialize source deletion with instance creation and its durable Git binding.
        async with self.lock:return await self.repositories.delete(value)
    async def delete(self,value):
        if not isinstance(value,dict) or set(value)!={'id','confirmed'} or value['confirmed'] is not True:raise Invalid('Confirm action deletion.')
        async with self.brokers.lock:
            async with self.lock:
                service=self.get(value['id']);service.ensure_idle()
                if self.devices.references(value['id']):raise Invalid('Remove this action from devices before deleting it.')
                candidate=copy.deepcopy(self.registry);definition=next(c for c in candidate['agents'] if c['id']==value['id'])
                candidate['agents']=[c for c in candidate['agents'] if c['id']!=value['id']];candidate.setdefault('deleted',[]).append(definition)
                self.store.write('agents.json',candidate);self.registry=candidate;service.deleted=True;del self.services[value['id']]
                await service.close()
                return self.list()
    async def start(self):
        self.home_assistant.start()
        self.repositories.start()
        await self.brokers.refresh();self.running=True
        self.devices.task=asyncio.create_task(self.devices.run())
        self.discovery.task=asyncio.create_task(self.discovery.run())
        for service in self.services.values():service.task=asyncio.create_task(service.run())
    async def close(self):
        self.running=False
        await self.devices.close()
        await self.repositories.close()
        for service in self.services.values():await service.close()
        await self.discovery.close()
        await self.home_assistant.close()
