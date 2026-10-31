"""Private, immutable per-action images and bounded disposable preview outputs."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import time
import uuid
from .domain import Invalid
from .store import Store

MAX_IMAGE = 8 * 1024 * 1024
ASSET_ID = re.compile(r'[a-f0-9]{64}\.(?:png|jpg)')
RUN_ID = re.compile(r'[a-f0-9]{32}')


def image_info(data):
    """Header validation only. Decoders must also impose pixel and decode limits."""
    if not data or len(data)>MAX_IMAGE:raise Invalid('Image must be at most 8 MiB.')
    width=height=0;extension=None
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=33 and data[12:16]==b'IHDR':
        width,height=struct.unpack('>II',data[16:24]);extension='png'
    elif data.startswith(b'\xff\xd8'):
        position=2
        while position+4<=len(data):
            if data[position]!=255:break
            while position<len(data) and data[position]==255:position+=1
            if position>=len(data):break
            marker=data[position];position+=1
            if marker in (0xd8,0xd9) or 0xd0<=marker<=0xd7:continue
            if marker==0xda:break
            if position+2>len(data):break
            length=int.from_bytes(data[position:position+2],'big')
            if length<2 or position+length>len(data):break
            if marker in (0xc0,0xc1,0xc2):
                if length<8:break
                height,width=struct.unpack('>HH',data[position+3:position+7]);extension='jpg';break
            position+=length
    if extension is None or not 1<=width<=8192 or not 1<=height<=8192 or width*height>16_000_000:
        raise Invalid('Use a PNG or JPEG image up to 16 megapixels and 8192 pixels per side.')
    return {'width':width,'height':height,'extension':extension,'size':len(data)}


class ActionAssets:
    def __init__(self,service):
        self.service=service
        self.store=Store(service.store.root/'assets')
        self.lock=asyncio.Lock()
    def require(self):
        if not self.service.environment or 'assets' not in self.service.plugin.manifest.get('capabilities',[]):
            raise Invalid('This action does not support private image assets.')
    def path(self,ident):
        self.require()
        if not isinstance(ident,str) or not ASSET_ID.fullmatch(ident):raise Invalid('Invalid image asset ID.')
        path=self.store.root/ident
        if path.is_symlink() or not path.is_file():raise Invalid('Image asset not found.')
        return path
    async def upload(self,value):
        self.require()
        if not isinstance(value,dict) or set(value)!={'base64'} or not isinstance(value['base64'],str):raise Invalid('Provide a base64 encoded image.')
        if len(value['base64'])>4*((MAX_IMAGE+2)//3):raise Invalid('Image must be at most 8 MiB.')
        try:data=base64.b64decode(value['base64'],validate=True)
        except Exception:raise Invalid('Invalid image encoding.') from None
        info=image_info(data);ident=hashlib.sha256(data).hexdigest()+'.'+info['extension']
        async with self.lock:
            files=[p for p in self.store.root.iterdir() if p.is_file()]
            if not (self.store.root/ident).exists():
                if len(files)>=128 or sum(p.stat().st_size for p in files)+len(data)>256*1024*1024:
                    raise Invalid('Private image storage is full (128 images or 256 MiB).')
                self.store.bytes(ident,data)
        return {'id':ident,**info}

    async def delete(self,value):
        self.require();self.service.ensure_idle()
        if not isinstance(value,dict) or set(value)!={'id'}:raise Invalid('Provide image asset ID.')
        async with self.service.lock:
            async with self.lock:
                path=self.path(value['id'])
                # Generic JSON references: protect saved draft, active and historical configurations/results.
                if value['id'] in json.dumps([self.service.config,self.service.jobs,self.service.device_configs()],ensure_ascii=True):
                    raise Invalid('Image is referenced by a saved configuration or execution history.')
                path.unlink()
        return {'deleted':True}


async def preview(service,value,*,camera=False,reference=False):
    service.ensure_idle()
    if not service.environment or 'preview' not in service.plugin.manifest.get('capabilities',[]):
        raise Invalid('This action does not declare an isolated preview contract.')
    if camera and 'home_assistant' not in service.plugin.manifest.get('capabilities',[]):raise Invalid('This action does not support camera tests.')
    if not isinstance(value,dict) or set(value)!={'settings','data'}:raise Invalid('Provide preview settings and data.')
    async with service.lock:
        await service.plugin.validate(value['settings'])
        await service.plugin.validate_input({'request_id':'preview','data':value['data']})
        root=service.store.root/'previews';root.mkdir(exist_ok=True,mode=0o700)
        # Preview outputs are disposable. Never touch jobs, active config or input assets.
        previous=sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()),key=lambda p:p.stat().st_mtime)
        for path in previous:
            if len(previous)>7 or time.time()-path.stat().st_mtime>3600:
                shutil.rmtree(path);previous=previous[1:]
        run=uuid.uuid4().hex;output=root/run;output.mkdir(mode=0o700)
        try:
            result=await service.environment.invoke(value['data'],value['settings'],'reference_capture' if reference else 'camera_test',output) if camera else await service.environment.preview(value['data'],value['settings'],output)
            files=[];size=0
            for path in output.iterdir():
                if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}\.(png|jpg)',path.name):
                    raise Invalid('Preview generated an unsupported output.')
                if path.stat().st_size>MAX_IMAGE:raise Invalid('Preview image is too large.')
                info=image_info(path.read_bytes());size+=info['size']
                if len(files)>=40 or size>32*1024*1024:raise Invalid('Preview output exceeds its limits.')
                files.append({'name':path.name,**info})
            assets=[]
            if reference and result.get('status')=='read':
                for file in sorted(files,key=lambda item:item['name']):
                    assets.append(await service.assets.upload({'base64':base64.b64encode((output/file['name']).read_bytes()).decode()}))
            return {'assets':assets,'mode':'preview','delivery':'not_sent','result':result,'run':run,'images':files}
        except BaseException:
            shutil.rmtree(output,ignore_errors=True);raise


def preview_path(service,run,name):
    if not RUN_ID.fullmatch(run) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}\.(png|jpg)',name):raise Invalid('Invalid preview image.')
    path=service.store.root/'previews'/run/name
    if path.is_symlink() or not path.is_file() or time.time()-path.parent.stat().st_mtime>3600:raise Invalid('Preview image expired. Run the preview again.')
    return path
