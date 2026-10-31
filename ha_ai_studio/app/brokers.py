"""Shared private broker registry. Discovered credentials remain in memory."""
import asyncio
import copy
import os
import uuid
from contextlib import AsyncExitStack
from .domain import Invalid, defaults, settings_check, public_settings
from .dev_config import dev_mqtt_settings
from .mqtt import credentials


def topic_check(value):
    if not isinstance(value,str) or not value or len(value.encode())>512 or any(c in value for c in ('+','#','\x00')) or any(ord(c)<32 for c in value):
        raise Invalid('Enter a concrete MQTT topic, up to 512 bytes, without wildcards.')
    if value.startswith(('$','homeassistant/')) or '/internal/' in value:
        raise Invalid('Choose a topic outside reserved discovery and internal namespaces.')


class Brokers:
    def __init__(self,store):
        self.store=store;self.lock=asyncio.Lock();self.manual=store.read('brokers.json',{})
        self.cleanup_connections=store.read('broker-cleanup.json',{})
        self.discovered={};self.services=lambda:[];self.discovery=None
        environment=dev_mqtt_settings()
        if environment and environment['mqtt']['host']:
            self.discovered['dev-environment']={'id':'dev-environment','name':'Development environment','source':'environment','settings':environment}

    def pin_cleanup(self, id, endpoint):
        key=id+':'+endpoint
        settings=self.get(id)['settings']
        if self.cleanup_connections.get(key)!=settings:
            candidate={**self.cleanup_connections,key:copy.deepcopy(settings)}
            self.store.write('broker-cleanup.json',candidate);self.cleanup_connections=candidate

    def cleanup_settings(self, id, endpoint):
        from .discovery import Discovery
        current=self.all().get(id)
        if current and (not endpoint or Discovery.settings_fingerprint(current['settings'])==endpoint):
            return current['settings']
        pinned=self.cleanup_connections.get(id+':'+str(endpoint))
        if pinned:return pinned
        raise Invalid('The previous broker connection is required to finish cleanup.')

    def prune_cleanup(self, owned):
        keys={i['broker_id']+':'+str(i['endpoint']) for i in owned}
        candidate={key:value for key,value in self.cleanup_connections.items() if key in keys}
        if candidate!=self.cleanup_connections:
            self.store.write('broker-cleanup.json',candidate);self.cleanup_connections=candidate

    def all(self):return {**self.manual,**self.discovered}

    def get(self,id):
        if not isinstance(id,str) or id not in self.all():raise Invalid('Select an available MQTT broker in Settings.')
        return self.all()[id]

    def endpoint(self,id):
        item=self.all().get(id)
        if not item:return id
        conf=item['settings']['mqtt']
        return (conf['host'].strip().lower().rstrip('.'),conf['port'])

    def check_topology(self, candidate=None, config=None):
        edges={}
        for service in self.services():
            if service is candidate:value=config
            elif service.enabled:value=service.config['active']
            else:continue
            route=service.routes(value)
            if self.discovery:self.discovery.check_routes(route)
            start=(self.endpoint(route['input_broker']),route['request_topic'])
            end=(self.endpoint(route['output_broker']),route['result_topic'])
            if start in edges:raise Invalid('Another active action uses this input broker and topic.')
            edges[start]=end
        if self.discovery:
            for service in self.services():
                for job in service.jobs.values():
                    if job.get('source') != 'device':self.discovery.check_routes(service.routes(job['config']))
        for start in edges:
            seen=set();node=start
            while node in edges:
                if node in seen:raise Invalid('Input and output routes form a feedback loop. Choose a different result destination.')
                seen.add(node);node=edges[node]

    def public(self):
        items=[]
        for item in self.all().values():
            safe=public_settings(item['settings'])
            items.append({'id':item['id'],'name':item['name'],'source':item['source'],'editable':item['source']=='manual',
                          'host':safe['settings']['mqtt']['host'],'port':safe['settings']['mqtt']['port'],
                          'tls':safe['settings']['mqtt']['tls'],'secrets':safe['secrets']})
        return {'brokers':items,'default_id':'home-assistant' if 'home-assistant' in self.discovered else None}

    async def refresh(self):
        async with self.lock:
            found=None
            if os.getenv('SUPERVISOR_TOKEN'):
                try:
                    conf=await asyncio.to_thread(credentials,defaults())
                    settings=settings_check({'mqtt':{'mode':'manual','host':conf['host'],'port':int(conf['port']),
                        'username':conf.get('username',''),'password':conf.get('password',''),'tls':bool(conf.get('tls',False))}},defaults())
                    found={'id':'home-assistant','name':'Home Assistant MQTT','source':'supervisor','settings':settings}
                except Exception:pass
            previous=self.discovered.get('home-assistant')
            if previous!=found:
                affected=[s for s in self.services() if s.references_broker('home-assistant')]
                async with self.guard(affected):
                    if found:self.discovered['home-assistant']=found
                    else:self.discovered.pop('home-assistant',None)
                    try:self.check_topology()
                    except Invalid:
                        if previous:self.discovered['home-assistant']=previous
                        else:self.discovered.pop('home-assistant',None)
                        raise
                    for service in affected:await service.disconnect()
            return self.public()

    def guard(self,services):
        class Guard:
            async def __aenter__(inner):
                if any(s.busy for s in services):
                    raise Invalid('An action is busy. Try again after the run.')
                inner.stack=AsyncExitStack()
                for service in services:await inner.stack.enter_async_context(service.lock)
                return inner
            async def __aexit__(inner,*args):await inner.stack.__aexit__(*args)
        return Guard()

    async def save(self,value):
        if not isinstance(value,dict) or set(value)!={'id','name','host','port','tls','username','password'}:raise Invalid('Provide broker name, connection and credentials.')
        id=value['id']
        if id is not None and (not isinstance(id,str) or id not in self.manual):raise Invalid('This broker cannot be edited.')
        if not isinstance(value['name'],str) or not 1<=len(value['name'].strip())<=80 or any(ord(c)<32 for c in value['name']):raise Invalid('Broker name must contain 1 to 80 characters.')
        async with self.lock:
            if id is not None and id not in self.manual:raise Invalid('Broker not found.')
            old=self.manual[id]['settings'] if id else defaults()
            settings=settings_check({'mqtt':{'mode':'manual',**{key:value[key] for key in ('host','port','tls','username','password')}}},old)
            if id is None:
                if len(self.manual)>=32:raise Invalid('Maximum of 32 manual brokers.')
                id='broker-'+uuid.uuid4().hex[:12]
            if self.discovery and self.discovery.references_broker(id) and any(settings['mqtt'][k]!=old['mqtt'][k] for k in ('host','port','tls')):
                raise Invalid('Disable discovery and complete cleanup before changing this broker endpoint.')
            affected=[s for s in self.services() if s.references_broker(id)]
            async with self.guard(affected):
                candidate=copy.deepcopy(self.manual);candidate[id]={'id':id,'name':value['name'].strip(),'source':'manual','settings':settings}
                previous=self.manual;self.manual=candidate
                try:self.check_topology()
                finally:self.manual=previous
                self.store.write('brokers.json',candidate);self.manual=candidate
                for service in affected:await service.disconnect()
            return self.public()

    async def delete(self,value):
        if not isinstance(value,dict) or set(value)!={'id','confirmed'} or value['confirmed'] is not True:raise Invalid('Confirm broker deletion.')
        async with self.lock:
            id=value['id']
            if not isinstance(id,str) or id not in self.manual:raise Invalid('Only manual brokers can be deleted.')
            if self.discovery and self.discovery.references_broker(id):raise Invalid('Broker is selected for discovery or required for pending cleanup. Clear its discovery selection and finish cleanup first.')
            if any(s.references_broker(id) for s in self.services()):raise Invalid('Broker is used by an action draft, active configuration or saved request. Change selections or remove the action first.')
            candidate=copy.deepcopy(self.manual);del candidate[id]
            self.store.write('brokers.json',candidate);self.manual=candidate
            return self.public()

    async def test(self,value):
        if not isinstance(value,dict) or set(value)!={'id'}:raise Invalid('Select a saved broker.')
        async with self.lock:settings=copy.deepcopy(self.get(value['id'])['settings'])
        from .broker_probe import probe
        return await asyncio.to_thread(probe,settings)
