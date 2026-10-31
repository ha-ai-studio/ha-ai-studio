"""Server-owned HA credentials and run-scoped, bounded entity operations."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile
from urllib.parse import urlsplit
import uuid
import aiohttp
from .domain import Invalid
from .store import Store
from .assets import image_info, MAX_IMAGE

ENTITY = re.compile(r'(camera|light|switch)\.[a-z0-9_]{1,200}')


def connection():
    if os.getenv('HA_AI_STUDIO_DEV') == '1':
        url, token = os.getenv('HA_URL','').rstrip('/'), os.getenv('HA_ACCESS_TOKEN','')
        if not url and not token:return None
        parsed=urlsplit(url)
        if not token or parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Invalid('Configure HA_URL and HA_ACCESS_TOKEN in the private development environment.')
        return url+'/api',token
    token=os.getenv('SUPERVISOR_TOKEN','')
    return ('http://supervisor/core/api',token) if token else None


def configured_entities(settings):
    # Entity access is restricted to IDs in the persisted execution configuration.
    if isinstance(settings,str):return {settings} if ENTITY.fullmatch(settings) else set()
    if isinstance(settings,dict):return set().union(set(),*(configured_entities(v) for v in settings.values()))
    if isinstance(settings,list):return set().union(set(),*(configured_entities(v) for v in settings))
    return set()


class HomeAssistant:
    def __init__(self, root):
        self.store=Store(Path(root)/'home-assistant')
        self.pending=self.store.read('restore.json',{})
        self.lock=asyncio.Lock();self.in_use=set();self.task=None

    async def request(self, method, path, data=None, image=False):
        config=connection()
        if config is None:raise Invalid('Home Assistant connection is unavailable. Configure HA_URL and HA_ACCESS_TOKEN for local development.')
        url,token=config
        try:
            family=socket.AF_INET if os.getenv('HA_AI_STUDIO_DEV')=='1' and os.getenv('HA_ADDRESS_FAMILY')=='ipv4' else socket.AF_UNSPEC
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=family),timeout=aiohttp.ClientTimeout(total=10),trust_env=False) as session:
                async with session.request(method,url+'/'+path,json=data,headers={'Authorization':'Bearer '+token},allow_redirects=False) as response:
                    if response.status in (401,403):raise Invalid('Home Assistant denied access. Check the server connection permissions.')
                    if response.status==404:raise Invalid('Home Assistant entity or operation is unavailable.')
                    if response.status!=200:raise Invalid(f'Home Assistant returned HTTP {response.status} during '+('camera image capture' if image else 'entity/service request')+'.')
                    limit=MAX_IMAGE if image else 8*1024*1024
                    chunks=[];size=0
                    async for chunk in response.content.iter_chunked(65536):
                        size+=len(chunk)
                        if size>limit:raise Invalid('Home Assistant response is too large.')
                        chunks.append(chunk)
                    raw=b''.join(chunks)
                    if image:image_info(raw);return raw
                    return json.loads(raw)
        except Invalid:raise
        except (aiohttp.ClientError,TimeoutError,ValueError):raise Invalid('Home Assistant is unreachable or returned an invalid response.') from None

    def fingerprint(self):
        config=connection()
        return hashlib.sha256(config[0].encode()).hexdigest() if config else ''

    async def search(self,domains,query):
        if not domains or not set(domains)<= {'camera','light','switch'} or len(query)>120:raise Invalid('Choose camera, light or switch entities and a search up to 120 characters.')
        states=await self.request('GET','states')
        if not isinstance(states,list):raise Invalid('Home Assistant returned an invalid entity list.')
        result=[]
        for item in states:
            ident=item.get('entity_id','');attrs=item.get('attributes',{})
            if not ENTITY.fullmatch(ident) or ident.split('.')[0] not in domains:continue
            name=str(attrs.get('friendly_name',ident))[:200];state=str(item.get('state','unknown'))[:80]
            if query.casefold() not in (ident+' '+name).casefold():continue
            result.append({'entity_id':ident,'name':name,'state':state,'available':state not in ('unknown','unavailable')})
        result.sort(key=lambda r:(r['name'].casefold(),r['entity_id']))
        return {'entities':result[:100],'total':len(result)}

    async def state(self,entity):
        value=await self.request('GET','states/'+entity)
        state=value.get('state','unknown')
        if state in ('unknown','unavailable'):raise Invalid('Selected Home Assistant entity is unavailable.')
        return {'entity_id':entity,'state':state}

    async def service(self,entity,action,**data):
        await self.request('POST','services/'+entity.split('.')[0]+'/'+action,{'entity_id':entity,**data})

    async def restore(self,entity):
        record=self.pending.get(entity)
        if not record:return
        if record['connection']!=self.fingerprint():raise Invalid('Light restoration is pending for a different Home Assistant connection.')
        # Legacy journal entries represent lights that were originally off.
        brightness=record.get('brightness')
        if brightness is not None:
            await self.service(entity,'turn_on',brightness=brightness)
        if record.get('state','off')=='off':
            await self.service(entity,'turn_off')
        candidate=dict(self.pending);candidate.pop(entity,None)
        self.store.write('restore.json',candidate);self.pending=candidate

    async def recover(self):
        while True:
            for entity in list(self.pending):
                async with self.lock:
                    if entity in self.in_use:continue
                    self.in_use.add(entity)
                try:await self.restore(entity)
                except Exception:pass  # Journal remains durable; no credentials or response bodies logged.
                finally:self.in_use.discard(entity)
            await asyncio.sleep(30)

    def start(self):self.task=asyncio.create_task(self.recover())
    async def close(self):
        if self.task:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)

    @asynccontextmanager
    async def session(self,settings):
        from .test_progress import current
        log=current.get() or (lambda message:None)
        allowed=configured_entities(settings)
        reserved=False
        touched=set();handlers=set();gate=asyncio.Lock();active=True;captures=0
        try:
            with tempfile.TemporaryDirectory(prefix='studio-ha-') as directory:
                socket_path=str(Path(directory)/'rpc');token=uuid.uuid4().hex
                async def handle(reader,writer):
                    nonlocal captures,reserved
                    current=asyncio.current_task();handlers.add(current)
                    try:
                        async with asyncio.timeout(25):
                            value=json.loads(await reader.readline())
                            async with gate:
                                if not active or value.get('token')!=token:raise Invalid('Home Assistant execution access has expired.')
                                entity=value.get('entity_id','');operation=value.get('operation')
                                if entity not in allowed:raise Invalid('Entity is not configured for this Action execution.')
                                if not reserved:
                                    async with self.lock:
                                        if allowed & (self.in_use | set(self.pending)):raise Invalid('A selected entity is busy or awaiting light restoration.')
                                        self.in_use.update(allowed);reserved=True
                                labels={'state':'Checking entity state','capture':'Fetching camera image','light_on':'Preparing illumination','restore':'Restoring illumination'}
                                if operation!='wait':log(labels.get(operation,'Device operation')+': '+entity)
                                if operation=='state':result=await self.state(entity)
                                elif operation=='wait' and entity.startswith('camera.'):
                                    seconds=value.get('seconds');phase=value.get('phase')
                                    if type(seconds) not in (int,float) or not 0<=seconds<=20 or phase not in ('before_capture','between_captures'):raise Invalid('Invalid capture wait event.')
                                    log(f'Waiting {seconds:g} seconds '+('before the first capture.' if phase=='before_capture' else 'before the next capture.'))
                                    result={'logged':True}
                                elif operation=='capture' and entity.startswith('camera.'):
                                    limit=3 if settings.get('capture',{}).get('discard_first') is True else 2
                                    if captures>=limit:raise Invalid('At most '+('three' if limit==3 else 'two')+' camera captures are allowed per execution.')
                                    captures+=1
                                    await self.state(entity)
                                    data=await self.request('GET','camera_proxy/'+entity,image=True)
                                    if captures==1 and settings.get('capture',{}).get('discard_first') is True:log('Warm-up camera image fetched; discarding the first image.')
                                    path=Path(directory)/f'capture-{captures}.img';path.write_bytes(data);path.chmod(0o600)
                                    result={'path':str(path)}
                                elif operation=='light_on' and entity.startswith(('light.','switch.')):
                                    state=await self.request('GET','states/'+entity)
                                    if state.get('state') not in ('on','off'):raise Invalid('Illumination entity must have an on/off state.')
                                    attrs=state.get('attributes',{})
                                    brightness=attrs.get('brightness')
                                    if type(brightness) is not int or not 0<=brightness<=255:brightness=None
                                    dimmable=entity.startswith('light.') and (brightness is not None or bool(set(attrs.get('supported_color_modes') or [])-{'onoff','unknown'}))
                                    if state['state']=='on' and dimmable and brightness is None:
                                        raise Invalid('Cannot safely restore illumination: current brightness is unavailable.')
                                    if entity not in touched and (state['state']=='off' or dimmable):
                                        candidate={**self.pending,entity:{'connection':self.fingerprint(),'state':state['state'],'brightness':brightness if dimmable else None}}
                                        self.store.write('restore.json',candidate);self.pending=candidate;touched.add(entity)
                                        await self.service(entity,'turn_on',**({'brightness':255} if dimmable else {}))
                                    result={'changed':entity in touched}
                                elif operation=='restore' and entity in touched:
                                    await self.restore(entity);touched.remove(entity);result={'restored':True}
                                elif operation=='restore':result={'restored':True}
                                else:raise Invalid('Unsupported Home Assistant operation.')
                                if operation!='wait':log(labels.get(operation,'Device operation')+' completed.')
                            response={'result':result}
                    except Invalid as error:
                        log('Failed: '+str(error));response={'error':str(error)}
                    except BaseException:response={'error':'Home Assistant operation failed.'}
                    try:
                        writer.write(json.dumps(response).encode()+b'\n');await writer.drain()
                    except (ConnectionError,RuntimeError):pass
                    finally:writer.close();handlers.discard(current)
                server=await asyncio.start_unix_server(handle,path=socket_path,limit=8192)
                try:yield {'socket':socket_path,'token':token}
                finally:
                    active=False;server.close();await server.wait_closed()
                    # Finish any in-flight service call before restoring the light.
                    if handlers:await asyncio.gather(*list(handlers),return_exceptions=True)
                    failure=False
                    for entity in list(touched):
                        try:
                            log('Cleanup: restoring illumination.')
                            await self.restore(entity)
                        except Exception:
                            log('Illumination restoration pending; Studio will retry.');failure=True
                    if failure:raise Invalid('Light restoration is pending. Studio will retry; check the light before another capture.')
        finally:
            if reserved:self.in_use.difference_update(allowed)
