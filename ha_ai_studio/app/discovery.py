"""Optional central MQTT discovery with durable, exact-topic ownership."""
import asyncio
import copy
import hashlib
import json
from .domain import Invalid
from .mqtt import MQTT

VERSION = '0.0.21'
DEFAULT = {'enabled': False, 'broker_id': None, 'prefix': 'homeassistant', 'ha_status_topic': 'homeassistant/status'}


class Discovery:
    def __init__(self, studio):
        self.studio = studio
        self.store = studio.store
        self.brokers = studio.brokers
        self.data = self.store.read('discovery.json')
        if self.data is None:
            self.data = {'config': dict(DEFAULT), 'owned': [], 'authorized': False}
            # Earlier versions announced one status entity on the input broker.
            # Record only exact known topics; reconcile after an explicit settings save.
            for service in studio.services.values():
                active = (service.config or {}).get('active')
                if active:
                    broker = active['input']['broker_id']
                    if broker:
                        prefix = service.identity['prefix']
                        self.data['owned'].append({'broker_id': broker, 'endpoint': None, 'topics': [
                            f'homeassistant/sensor/{prefix}/status/config', prefix+'/status', prefix+'/receiver']})
            self.persist()
        self.lock = asyncio.Lock()
        self.client = None
        self.connection = None
        self.task = None
        self.stopping = False
        self.status = 'Disabled'
        self.sent = {}
        self.birth = False

    def persist(self):
        try:
            self.store.write('discovery.json', self.data)
        except Exception:
            self.data = self.store.read('discovery.json', {'config': dict(DEFAULT), 'owned': [], 'authorized': False})
            raise

    def references_broker(self, broker):
        return (self.data['config']['broker_id'] == broker or
                any(item['broker_id'] == broker for item in self.data['owned']))

    def public(self):
        desired = self.desired() if self.data['config']['enabled'] else {}
        selected = self.data['config']['broker_id']
        try: endpoint = self.fingerprint(selected)
        except Invalid: endpoint = None
        pending = sum(len(set(item['topics']) - (set(desired) if item['broker_id'] == selected and item['endpoint'] in (None, endpoint) else set())) for item in self.data['owned'])
        return {'config': copy.deepcopy(self.data['config']), 'status': self.status, 'instance_id': self.studio.instance_id,
                'pending_cleanup': pending, 'legacy_pending': not self.data['authorized'] and bool(self.data['owned'])}

    async def save(self, value):
        if not isinstance(value, dict) or set(value) != set(DEFAULT) or type(value['enabled']) is not bool:
            raise Invalid('Provide discovery enabled, broker, prefix and Home Assistant status topic.')
        for key in ('prefix', 'ha_status_topic'):
            text = value[key]
            if not isinstance(text, str) or not text or len(text.encode()) > 128 or text.startswith('$') or any(c in text for c in '#+\x00') or any(ord(c) < 32 for c in text) or text.endswith('/'):
                raise Invalid('Use concrete discovery topics without wildcards or a trailing slash.')
        if value['broker_id'] is not None and not isinstance(value['broker_id'], str):
            raise Invalid('Select a discovery broker.')
        async with self.brokers.lock:
            if value['enabled'] or (value['broker_id'] is not None and not self.references_broker(value['broker_id'])):
                self.brokers.get(value['broker_id'])
            async with self.lock:
                previous = self.data['config']
                self.data['config'] = copy.deepcopy(value)
                try:
                    self.brokers.check_topology()
                    if value['ha_status_topic'] in set(self.desired()) | set(self.command_routes()):raise Invalid('Home Assistant status topic must not be a Studio-owned topic.')
                except Exception:
                    self.data['config'] = previous
                    raise
                await self.disconnect()
                self.data['authorized'] = True
                self.persist()
                self.status = 'Applying settings; cleanup will be retried if necessary'
        return self.public()

    def desired(self):
        config = self.data['config']
        availability = self.studio.namespace + '/discovery/availability'
        result = {}
        for item in self.studio.devices.data['items'].values():
            active = item['active']
            if not active or not active['publish']:continue
            prefix = item['identity']
            device = {'identifiers': [prefix], 'name': active['name'],
                      'manufacturer': 'HA AI Studio', 'model': 'Virtual device', 'sw_version': VERSION}
            def entity(key, label, **extra):
                topic = prefix + '/' + key
                result[f"{config['prefix']}/sensor/{prefix}/{key}/config"] = {
                    'unique_id': prefix + '_' + key, 'name': label, 'device': device,
                    'state_topic': topic, 'availability_topic': availability, **extra}
                return topic
            result[entity('status', 'Device status', entity_category='diagnostic')] = item['status']
            for function in active['functions']:
                key = 'function_' + function['id']
                result[f"{config['prefix']}/button/{prefix}/{key}/config"] = {
                    'unique_id': prefix + '_' + key, 'name': function['name'], 'device': device,
                    'command_topic': self.command_topic(item, function), 'payload_press': 'execute',
                    'command_template': '{{ {"request_id": now().isoformat() ~ "-" ~ (range(0, 100000) | random)} | to_json }}',
                    'qos': 1, 'retain': False,
                    'availability_mode': 'all', 'availability': [
                        {'topic': availability}, {'topic': prefix + '/function_availability'}]}
                result[prefix + '/function_availability'] = 'online' if item['enabled'] and not self.studio.devices.busy(item['id']) else 'offline'
            for sensor in active['sensors']:
                key = 'value_' + sensor['id']
                value = item['values'].get(sensor['id'], {})
                extra = {}
                if type(value.get('value')) in (int, float):
                    extra['state_class'] = 'total_increasing' if sensor['cumulative'] else 'measurement'
                if sensor['device_class']:extra['device_class'] = sensor['device_class']
                if sensor['unit']:extra['unit_of_measurement'] = sensor['unit']
                if 'rate' in sensor:
                    extra['state_class'] = 'measurement'
                    extra['availability'] = [{'topic': availability}, {'topic': prefix + '/available_' + sensor['id']}]
                    extra['availability_mode'] = 'all'
                topic = entity(key, sensor['name'], **extra)
                if 'rate' in sensor:
                    result[f"{config['prefix']}/sensor/{prefix}/{key}/config"].pop('availability_topic', None)
                result[topic] = str(value['value']) if value.get('value') is not None else 'None'
                if 'rate' in sensor:
                    result[prefix + '/available_' + sensor['id']] = 'online' if item['enabled'] and value.get('status') == 'ready' else 'offline'
                result[entity('updated_' + sensor['id'], sensor['name'] + ' last successful reading',
                    device_class='timestamp', entity_category='diagnostic')] = value.get('timestamp', 'None')
                result[entity('status_' + sensor['id'], sensor['name'] + ' status', entity_category='diagnostic')] = (
                    'disabled' if not item['enabled'] else value.get('status', 'waiting'))
                # HA sensor states are limited to 255 characters; keep the full error in Studio.
                message = value.get('error') or ('Reading successful.' if value.get('status') == 'ready' else 'No reading yet.')
                result[entity('status_message_' + sensor['id'], sensor['name'] + ' Status Message',
                    entity_category='diagnostic')] = message if len(message) <= 255 else message[:254] + '…'
        # This is a shared transport availability topic, not an advertised Studio device.
        result[availability] = 'online'
        return result

    @staticmethod
    def command_topic(item, function):
        return item['identity'] + '/function/' + function['id'] + '/request'

    def command_routes(self, enabled_only=False):
        result = {}
        for item in self.studio.devices.data['items'].values():
            config = item['active']
            if not config or not config['publish'] or (enabled_only and not item['enabled']):continue
            for function in config['functions']:
                result[self.command_topic(item, function)] = (item['id'], function['id'])
        return result

    def receive_command(self, broker, topic, payload):
        # Recheck current authorization: queued callbacks may outlive a reconfiguration.
        config = self.data['config']
        if not config['enabled'] or config['broker_id'] != broker:return
        route = self.command_routes(enabled_only=True).get(topic)
        if not route:return
        try:
            value = json.loads(payload)
            if not isinstance(value, dict) or set(value) != {'request_id'}:raise Invalid('Invalid function command.')
            self.studio.devices.invoke_function(route[0], {'function_id': route[1], **value}, remote=True)
        except Exception:
            self.studio.devices.log(route[0], 'Function command rejected. Check request ID, active configuration and device availability.')

    def check_routes(self, route):
        config = self.data['config']
        targets = [(i['broker_id'], set(i['topics']) | set(i.get('commands', []))) for i in self.data['owned']]
        if config['enabled']:
            targets.append((config['broker_id'], set(self.desired()) | set(self.command_routes()) | {config['ha_status_topic']}))
        for broker, topics in targets:
            for side, key in (('input_broker', 'request_topic'), ('output_broker', 'result_topic')):
                if self.brokers.endpoint(route[side]) == self.brokers.endpoint(broker) and route[key] in topics:
                    raise Invalid('Action topics must not overlap discovery, diagnostic or Home Assistant status topics on this broker.')

    def fingerprint(self, broker):
        return self.settings_fingerprint(self.brokers.get(broker)['settings'])

    @staticmethod
    def settings_fingerprint(settings):
        conf = settings['mqtt']
        # No credentials or addresses in browser responses; endpoint guard prevents wrong-server cleanup.
        return hashlib.sha256(json.dumps([conf['host'].lower().rstrip('.'), conf['port'], conf['tls']]).encode()).hexdigest()

    async def disconnect(self):
        if self.client:
            await asyncio.to_thread(self.client.close)
        self.client = None
        self.connection = None
        self.sent = {}

    async def reconcile(self):
        async with self.brokers.lock:
            async with self.lock:
                if not self.data['authorized']:return
                config = self.data['config']
                self.brokers.check_topology()
                desired = self.desired() if config['enabled'] else {}
                broker = config['broker_id']
                # Clean old ownership before announcing new state. Never wildcard-delete.
                for item in list(self.data['owned']):
                    try: current_endpoint = self.fingerprint(broker) if config['enabled'] else None
                    except Invalid: current_endpoint = None
                    same_target = bool(config['enabled'] and current_endpoint and item['broker_id'] == broker and item['endpoint'] in (None, current_endpoint))
                    obsolete = set(item['topics']) - (set(desired) if same_target else set())
                    if not obsolete:continue
                    cleanup_settings = self.brokers.cleanup_settings(item['broker_id'], item['endpoint'])
                    await self.disconnect()
                    probe = MQTT({'prefix': self.studio.namespace+'_cleanup', 'device_id': 'cleanup'}, lambda _: None, subscribe=False)
                    try:
                        await asyncio.to_thread(probe.connect, cleanup_settings)
                        for topic in sorted(obsolete, key=lambda t: (not t.endswith('/config'), t)):
                            await asyncio.to_thread(probe.publish, topic, '', absolute=True, retain=True)
                            item['topics'].remove(topic)
                            self.persist()
                    finally:
                        await asyncio.to_thread(probe.close)
                    if not item['topics']:
                        self.data['owned'].remove(item)
                        self.persist()
                if not config['enabled']:
                    await self.disconnect()
                    self.brokers.prune_cleanup(self.data['owned'])
                    self.status = 'Disabled · owned discovery messages cleared'
                    return
                fingerprint = self.fingerprint(broker)
                record = next((i for i in self.data['owned'] if i['broker_id'] == broker), None)
                if record and record['endpoint'] and record['endpoint'] != fingerprint:
                    raise Invalid('Restore the previous broker endpoint to finish cleanup.')
                if not record:
                    record = {'broker_id': broker, 'endpoint': fingerprint, 'topics': [], 'commands': []}
                    self.data['owned'].append(record)
                commands = self.command_routes(enabled_only=True)
                command_topics = sorted(self.command_routes())
                commands_changed = record.get('commands', []) != command_topics
                record['commands'] = command_topics
                endpoint_changed = record['endpoint'] != fingerprint
                record['endpoint'] = fingerprint
                self.brokers.pin_cleanup(broker, fingerprint)
                topics = sorted(set(record['topics']) | set(desired))
                if topics != record['topics'] or endpoint_changed or commands_changed:
                    record['topics'] = topics
                    self.persist()  # Write ownership BEFORE publication, including partial failures.
                settings = self.brokers.get(broker)['settings']
                signature = json.dumps([settings, sorted(commands), config['ha_status_topic']], sort_keys=True)
                if not self.client or self.connection != signature:
                    await self.disconnect()
                    loop = asyncio.get_running_loop()
                    def birth(payload):
                        if payload == b'online':loop.call_soon_threadsafe(setattr, self, 'birth', True)
                    self.client = MQTT({'prefix': self.studio.namespace+'_discovery', 'device_id': self.studio.namespace,
                        'request_topic': config['ha_status_topic'], 'accept_retained': True,
                        'availability_topic': self.studio.namespace+'/discovery/availability'}, birth,
                        commands={topic: (lambda payload, topic=topic: loop.call_soon_threadsafe(self.receive_command, broker, topic, payload)) for topic in commands})
                    await asyncio.to_thread(self.client.connect, settings)
                    self.connection = signature
                if not self.client.ready.is_set():raise Invalid('Discovery is reconnecting.')
                if self.client.needs_discovery or self.birth:
                    self.sent = {};self.birth = False;self.client.needs_discovery = False
                for topic, payload in desired.items():
                    if self.sent.get(topic) != payload:
                        await asyncio.to_thread(self.client.publish, topic, payload, absolute=True, retain=True)
                        self.sent[topic] = copy.deepcopy(payload)
                self.status = 'Connected · discovery published'
                self.brokers.prune_cleanup(self.data['owned'])

    async def run(self):
        while not self.stopping:
            try:await self.reconcile()
            except Exception:
                self.status = 'Connection or publication failed · pending cleanup is retained; check broker access and original endpoint'
                async with self.lock:await self.disconnect()
            await asyncio.sleep(0.5)

    async def close(self):
        self.stopping = True
        # Let in-flight thread operations finish before disconnecting (no orphan publisher).
        if self.task:await self.task
        async with self.lock:await self.disconnect()
