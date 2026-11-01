import logging
import os
import re
from pathlib import Path
from aiohttp import web
from .domain import Invalid
from .actions import Studio
from .dev_config import dev_mqtt_settings

STATIC = Path(__file__).parent / 'static'


def create_app(root=None, dev=None):
    dev = os.getenv('HA_AI_STUDIO_DEV') == '1' if dev is None else dev
    dev_mqtt_settings()  # Fail closed on invalid real-MQTT dev settings, even with no actions.
    studio = Studio(root or os.getenv('HA_AI_STUDIO_DATA', '/data'))

    @web.middleware
    async def boundary(request, handler):
        if not dev and request.remote != '172.30.32.2':
            return web.json_response({'error': 'Access is only allowed through Home Assistant Ingress.'}, status=403)
        if dev and request.host.split(':')[0] not in ('127.0.0.1', 'localhost', '[::1]'):
            return web.json_response({'error': 'Dev access is only allowed through localhost.'}, status=403)
        if request.method not in ('GET', 'HEAD'):
            if request.headers.get('X-HA-AI-Studio-Request') != '1' or request.content_type != 'application/json' or request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return web.json_response({'error': 'Invalid browser request.'}, status=403)
        try:
            response = await handler(request)
        except Invalid as error:
            response = web.json_response({'error': str(error)}, status=400)
        except web.HTTPException:
            raise
        except Exception:
            # Never serialize exception text, credentials, raw request bodies or library tracebacks.
            response = web.json_response({'error': 'Operation failed. Check the connection and settings.'}, status=400)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"})
        return response

    app = web.Application(middlewares=[boundary], client_max_size=100_000)
    app[web.AppKey('studio', Studio)] = studio

    async def index(request):
        return web.FileResponse(STATIC / 'index.html')

    async def static(request):
        name = request.match_info['name']
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.(?:js|css)', name):
            raise web.HTTPNotFound()
        path = STATIC / 'static' / name
        if not path.is_file() or path.is_symlink():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def api(request):
        action = request.match_info['action']
        if action in ('action-asset','action-asset-delete','action-preview','action-camera-test','action-camera-reference','preview-image'):
            from .assets import preview,preview_path
            service=studio.get(request.query.get('id'))
            if action=='action-asset':
                if request.method=='GET':return web.FileResponse(service.assets.path(request.query.get('asset')))
                if request.method=='POST':
                    upload=request.clone(client_max_size=12*1024*1024)
                    return web.json_response(await service.assets.upload(await upload.json()))
            if action=='action-asset-delete' and request.method=='POST':return web.json_response(await service.assets.delete(await request.json()))
            if action in ('action-preview','action-camera-test','action-camera-reference') and request.method=='POST':
                value=await request.json()
                if action in ('action-camera-test','action-camera-reference') and isinstance(value,dict) and 'progress_id' in value:
                    from .test_progress import begin,current
                    service.ensure_idle()
                    log=begin(service,value.pop('progress_id'));token=current.set(log)
                    try:
                        log('Camera test started. Validating configuration.')
                        result=await preview(service,value,camera=True,reference=action=='action-camera-reference')
                        log('Camera test completed.' if result['result'].get('status')=='read' else 'Camera test completed with unreadable results.')
                        return web.json_response(result)
                    except Invalid as error:log('Failed: '+str(error));raise
                    except BaseException:log('Camera test failed or was cancelled. Check illumination restoration.');raise
                    finally:current.reset(token)
                return web.json_response(await preview(service,value,camera=action!='action-preview',reference=action=='action-camera-reference'))
            if action=='preview-image' and request.method=='GET':
                return web.FileResponse(preview_path(service,request.query.get('run',''),request.query.get('name','')))
            raise web.HTTPNotFound()
        if action=='camera-test-progress' and request.method=='GET':
            from .test_progress import read
            return web.json_response(read(studio.get(request.query.get('id')),request.query.get('run','')))
        if action=='ha-entities' and request.method=='GET':
            return web.json_response(await studio.home_assistant.search(request.query.get('domains','camera,light,switch').split(','),request.query.get('q','')))
        if action == 'device-template' and request.method == 'GET':
            return web.json_response(studio.devices.export_template(request.query.get('id')))
        if action == 'device-template-preview' and request.method == 'POST':
            config = studio.devices.template_config(await request.json())
            return web.json_response({'format': 'ha-ai-studio-device-template', 'version': 1, 'config': config})
        if action == 'device-template-import' and request.method == 'POST':
            return web.json_response(studio.devices.import_template(await request.json()))
        if action == 'devices':
            if request.method == 'GET':return web.json_response(studio.devices.list())
            if request.method == 'POST':return web.json_response(studio.devices.create(await request.json()))
        if action == 'delete-device' and request.method == 'POST':
            return web.json_response(studio.devices.delete(await request.json()))
        if action in ('device', 'device-deactivate', 'device-read', 'device-function'):
            id = request.query.get('id')
            if request.method == 'GET' and action == 'device':return web.json_response(studio.devices.public(id))
            if request.method == 'POST':
                if action == 'device':result = await studio.devices.save(id, await request.json())
                elif action == 'device-deactivate':result = studio.devices.deactivate(id)
                elif action == 'device-function':result = studio.devices.invoke_function(id, await request.json())
                else:result = studio.devices.start_read(id)
                return web.json_response(result)
            raise web.HTTPMethodNotAllowed(request.method, ['POST'])
        if action == 'repositories':
            if request.method=='GET':return web.json_response(studio.repositories.public())
            if request.method=='POST':return web.json_response(await studio.repositories.save(await request.json()))
        if action == 'repository-delete' and request.method=='POST':
            return web.json_response(await studio.delete_repository(await request.json()))
        if action in ('repository-actions-preview','repository-actions-update') and request.method=='POST':
            return web.json_response(await studio.repository_actions(await request.json(),apply=action=='repository-actions-update'))
        if action == 'action-version' and request.method=='POST':
            return web.json_response(await studio.change_version(request.query.get('id'),await request.json()))
        if action in ('repository-refresh','repository-check') and request.method=='POST':
            value=await request.json()
            if not isinstance(value,dict) or set(value)!={'id'} or not isinstance(value['id'],str):raise Invalid('Provide repository ID.')
            if action=='repository-check':studio.repositories.check(value['id'])
            else:studio.repositories.refresh(value['id'])
            return web.json_response(studio.repositories.public())
        if action == 'reload-source' and request.method=='POST':
            return web.json_response(await studio.reload_source(request.query.get('id')))
        if action == 'environment' and request.method=='POST':
            service=studio.get(request.query.get('id'));service.ensure_idle()
            if not service.environment:raise Invalid('Built-in actions need no installation.')
            if service.enabled:raise Invalid('Deactivate the action before rebuilding its environment.')
            service.environment.start();return web.json_response(service.status())
        if action == 'discovery':
            if request.method == 'GET':return web.json_response(studio.discovery.public())
            if request.method == 'POST':return web.json_response(await studio.discovery.save(await request.json()))
        if action == 'actions':
            if request.method == 'GET':return web.json_response(studio.list())
            if request.method == 'POST':return web.json_response(await studio.create(await request.json()))
        if action == 'delete-action' and request.method == 'POST':
            return web.json_response(await studio.delete(await request.json()))
        if action in ('brokers','broker-refresh','broker-delete','broker-test'):
            if action=='brokers' and request.method=='GET':return web.json_response(studio.brokers.public())
            if request.method=='POST':
                value=await request.json()
                if action=='brokers':result=await studio.brokers.save(value)
                elif action=='broker-refresh':result=await studio.brokers.refresh()
                elif action=='broker-delete':result=await studio.brokers.delete(value)
                else:result=await studio.brokers.test(value)
                return web.json_response(result)
            raise web.HTTPNotFound()
        if action not in ('action','deactivate','test','live'):raise web.HTTPNotFound()
        service=studio.get(request.query.get('id'))
        if request.method=='GET' and action=='live':
            try:after=int(request.query.get('after','0'))
            except ValueError:raise Invalid('Invalid event cursor.')
            if after<0:raise Invalid('Invalid event cursor.')
            return web.json_response(service.live(after))
        if action=='live':raise web.HTTPMethodNotAllowed(request.method,['GET'])
        if request.method=='GET' and action=='action':return web.json_response(service.status())
        if request.method=='POST':
            value=await request.json()
            if not isinstance(value,dict):raise Invalid('Provide a JSON object.')
            if action=='action':
                if 'enabled' in value:
                    if type(value['enabled']) is not bool or 'activate' in value:raise Invalid('Provide one boolean Active state.')
                    result=await service.save(value.get('config'),value['enabled'],set_enabled=value['enabled'],expected_snapshot=value.get('snapshot'))
                else:result=await service.save(value.get('config'),value.get('activate'))
            elif action=='deactivate':result=await service.deactivate()
            else:
                payload=value.get('payload')
                if not isinstance(payload,str):raise Invalid('Provide the test request as JSON text.')
                result=await service.test(value.get('config'),payload)
            return web.json_response(result)
        raise web.HTTPNotFound()

    async def type_editor(request):
        from .action_types import TYPES
        kind=request.match_info['kind']
        if kind not in TYPES:raise web.HTTPNotFound()
        return web.FileResponse(Path(TYPES[kind].__file__).parent/'editor.js')

    async def plugin_asset(request):
        from .plugins import local_file
        snapshot=request.match_info['snapshot'];kind=request.match_info['kind'];name=request.match_info['name']
        # Only explicitly declared UI trees in currently bound instances are public.
        plugin=next((s.plugin for s in studio.services.values() if s.environment and
                     s.plugin.record['snapshot']==snapshot and s.type==kind),None)
        if plugin is None or not plugin.manifest.get('editor') or not name.startswith('ui/'):
            raise web.HTTPNotFound()
        if any(part.startswith('.') for part in Path(name).parts) or Path(name).suffix not in ('.js','.css','.svg','.png','.jpg','.webp','.woff2'):
            raise web.HTTPNotFound()
        try:path=local_file(plugin.source,name)
        except Invalid:raise web.HTTPNotFound() from None
        return web.FileResponse(path)

    app.router.add_get('/plugin-assets/{snapshot}/{kind}/{name:.*}',plugin_asset)
    app.router.add_get('/action-types/{kind}/editor.js',type_editor)
    app.router.add_get('/', index)
    app.router.add_get('/static/{name}', static)
    app.router.add_route('*', '/api/{action}', api)

    async def lifecycle(app):
        await studio.start()
        yield
        await studio.close()
    app.cleanup_ctx.append(lifecycle)
    return app


if __name__ == '__main__':
    # Network exceptions may include credentials; do not forward library logs.
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    web.run_app(create_app(), host='0.0.0.0', port=8099, access_log=None, print=None, shutdown_timeout=210)
