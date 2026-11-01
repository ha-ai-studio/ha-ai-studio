"""Virtual devices: independent measurements, durable schedules and stable identities."""
import asyncio
import copy
import hashlib
import json
import math
import re
import time
import uuid
from datetime import datetime, timezone
from contextlib import AsyncExitStack
from .domain import Invalid
from .store import Store

MAX_FUNCTION_REQUESTS = 256

def migrate_sensor_profiles(data):
    """Preserve existing HA identities/metadata while removing Studio-specific types."""
    legacy = {'water': ('water', True), 'temperature': ('temperature', False),
              'humidity': ('humidity', False), 'number': ('', False)}
    for item in [*data['items'].values(), *data['deleted']]:
        for config in (item['draft'], item['active']):
            if not config:continue
            config.setdefault('functions', [])
            for function in config['functions']:function.pop('publish', None)
            config.pop('type', None)  # Early development drafts had a device preset.
            for sensor in config['sensors']:
                if 'kind' in sensor:
                    device_class, cumulative = legacy[sensor.pop('kind')]
                    sensor.update(device_class=device_class, cumulative=cumulative)
        for key, semantics in item['sensor_identities'].items():
            if len(semantics) == 2:
                device_class, cumulative = legacy[semantics[0]]
                item['sensor_identities'][key] = [device_class, semantics[1], cumulative]


def timestamp(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat()


def name(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 80 and not any(ord(c) < 32 for c in value)


def identifier(value):
    return isinstance(value, str) and re.fullmatch('[a-z][a-z0-9-]{0,47}', value)


class Devices:
    def __init__(self, studio):
        self.studio = studio
        self.store = Store(studio.store.root / 'devices')
        self.data = self.store.read('devices.json', {'items': {}, 'deleted': []})
        migrate_sensor_profiles(self.data)
        for item in [*self.data['items'].values(), *self.data['deleted']]:
            item.setdefault('function_requests', {})
        self.tasks = {}
        self.logs = {}
        self.editing = set()
        self.task = None
        self.stopping = False
        self.error = None
        for item in self.data['items'].values():
            if item['status'] == 'running':
                item['status'] = 'interrupted'
                item['next_run'] = time.time() + (item['active'] or item['draft'])['interval_minutes'] * 60 if (item['active'] or item['draft'])['sensors'] else None
        self.commit(self.data)

    def commit(self, candidate):
        self.store.write('devices.json', candidate)
        self.data = candidate

    def get(self, id):
        if not isinstance(id, str) or id not in self.data['items']:
            raise Invalid('Device not found.')
        return self.data['items'][id]

    def busy(self, id):
        return id in self.tasks and not self.tasks[id].done()

    def public(self, id):
        item = self.get(id)
        result = {k: copy.deepcopy(item[k]) for k in ('id', 'identity', 'draft', 'active', 'enabled', 'values', 'status', 'last_attempt', 'next_run')}
        result['logs'] = copy.deepcopy(self.logs.get(id, []))
        result['busy'] = self.busy(id)
        result['issue'] = self.error
        result['publication'] = self.studio.discovery.status if item['active'] and item['active']['publish'] else 'Not published'
        return result

    def log(self, id, message):
        entries = self.logs.setdefault(id, [])
        entries.append({'time': timestamp(), 'message': message})
        del entries[:-200]

    def list(self):
        return {'devices': [self.public(id) for id in self.data['items']]}

    def references(self, action_id, active_only=False):
        for item in self.data['items'].values():
            configs = [item['active']] if active_only else [item['draft'], item['active']]
            if active_only and not item['enabled']:
                continue
            if any(c and any(s['action_id'] == action_id for s in c['sensors'] + c['functions']) for c in configs):
                return True
        return False

    def action_configs(self, action_id):
        items = [*self.data['items'].values(), *self.data['deleted']]
        return [item['bindings'][action_id]['config'] for item in items if action_id in item['bindings']]

    def create(self, value):
        if not isinstance(value, dict) or set(value) != {'name'} or not name(value['name']):
            raise Invalid('Provide a device name.')
        if len(self.data['items']) >= 32:
            raise Invalid('Maximum of 32 devices.')
        config = {'name': value['name'].strip(), 'interval_minutes': 30,
                  'publish': False, 'sensors': [], 'functions': []}
        return self.create_config(config)

    def create_config(self, config):
        if len(self.data['items']) >= 32:
            raise Invalid('Maximum of 32 devices.')
        id = 'device-' + uuid.uuid4().hex[:16]
        while id in self.data['items'] or any(item['id'] == id for item in self.data['deleted']):
            id = 'device-' + uuid.uuid4().hex[:16]
        candidate = copy.deepcopy(self.data)
        candidate['items'][id] = {'id': id, 'identity': self.studio.namespace + '_' + id,
            'draft': config, 'active': None, 'enabled': False, 'bindings': {}, 'values': {},
            'status': 'disabled', 'last_attempt': None, 'next_run': None, 'sensor_identities': {}, 'function_requests': {}}
        self.commit(candidate)
        return self.public(id)

    def export_template(self, id):
        config = copy.deepcopy(self.get(id)['draft'])
        # Transfer configuration only, never runtime state, identities or Action settings.
        config['publish'] = False
        bindings = {}
        for entry in config['sensors'] + config['functions']:
            if 'rate' in entry:continue
            action_id = entry['action_id']
            if action_id not in bindings:bindings[action_id] = 'action-' + str(len(bindings) + 1)
            entry['action_id'] = bindings[action_id]
        return {'format': 'ha-ai-studio-device-template', 'version': 1, 'config': config}

    def template_config(self, value):
        if not isinstance(value, dict) or set(value) != {'format', 'version', 'config'}:
            raise Invalid('Provide a Device template file.')
        if value['format'] != 'ha-ai-studio-device-template' or type(value['version']) is not int or value['version'] != 1:
            raise Invalid('Unsupported Device template format or version.')
        # Validate the portable structure without requiring installation-specific Actions.
        config = self.validate(value['config'], template=True)
        if config['publish']:
            raise Invalid('Device templates must have publication disabled.')
        return config

    def import_template(self, value):
        if not isinstance(value, dict) or set(value) != {'template', 'name', 'actions'} or not name(value['name']):
            raise Invalid('Provide a template, device name and Action assignments.')
        config = self.template_config(value['template'])
        entries = [entry for entry in config['sensors'] + config['functions'] if 'rate' not in entry]
        slots = {entry['action_id'] for entry in entries}
        assignments = value['actions']
        if not isinstance(assignments, dict) or set(assignments) != slots or any(not isinstance(v, str) for v in assignments.values()):
            raise Invalid('Assign an existing Action to every template Action slot.')
        for entry in entries:entry['action_id'] = assignments[entry['action_id']]
        config['name'] = value['name'].strip()
        config['publish'] = False
        return self.create_config(self.validate(config))

    def validate(self, config, *, template=False):
        config = copy.deepcopy(config)
        if isinstance(config, dict):config.setdefault('functions', [])
        if not isinstance(config, dict) or set(config) != {'name', 'interval_minutes', 'publish', 'sensors', 'functions'}:
            raise Invalid('Provide the complete device configuration.')
        if not name(config['name']):
            raise Invalid('Invalid device name.')
        if type(config['interval_minutes']) is not int or not 1 <= config['interval_minutes'] <= 10080:
            raise Invalid('Update interval must be 1–10080 whole minutes.')
        if type(config['publish']) is not bool or not isinstance(config['sensors'], list) or len(config['sensors']) > 16:
            raise Invalid('Provide publication and up to 16 sensors.')
        ids = set()
        for sensor in config['sensors']:
            if not isinstance(sensor, dict) or set(sensor) not in ({'id', 'name', 'device_class', 'cumulative', 'unit', 'action_id', 'reading', 'max_change'}, {'id', 'name', 'device_class', 'cumulative', 'unit', 'action_id', 'reading', 'max_change', 'rate'}):
                raise Invalid('Provide the complete sensor definition.')
            if not identifier(sensor['id']) or sensor['id'] in ids or not name(sensor['name']):
                raise Invalid('Sensor IDs must be unique lowercase names; provide a sensor name.')
            ids.add(sensor['id'])
            if 'rate' not in sensor and not identifier(sensor['reading']):
                raise Invalid('Select a named action reading.')
            if type(sensor['cumulative']) is not bool:
                raise Invalid('Specify whether this is a cumulative counter.')
            if not isinstance(sensor['device_class'], str) or (sensor['device_class'] and not re.fullmatch('[a-z][a-z0-9_]{0,63}', sensor['device_class'])):
                raise Invalid('Use a Home Assistant device class identifier or leave it empty.')
            if not isinstance(sensor['unit'], str) or len(sensor['unit']) > 24 or any(ord(c) < 32 for c in sensor['unit']):
                raise Invalid('Use a unit of up to 24 characters.')
            maximum = sensor['max_change']
            if maximum is not None and (type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum <= 0):
                raise Invalid('Maximum change must be a positive finite number or empty.')
            if 'rate' in sensor:
                rate = sensor['rate']
                if not isinstance(rate, dict) or set(rate) != {'source', 'factor', 'time_base'}:
                    raise Invalid('Provide the source, conversion factor and time base for the rate.')
                if not identifier(rate['source']):raise Invalid('Select a source sensor.')
                if type(rate['factor']) not in (int, float) or not math.isfinite(rate['factor']) or rate['factor'] <= 0:
                    raise Invalid('Conversion factor must be a positive finite number.')
                if type(rate['time_base']) is not str or rate['time_base'] not in ('second', 'minute', 'hour'):
                    raise Invalid('Select second, minute or hour as the time base.')
                if sensor['action_id'] != '' or sensor['reading'] != '' or sensor['cumulative']:
                    raise Invalid('Rate sensors cannot have an Action reading or be cumulative counters.')
                continue
            if template:
                if not identifier(sensor['action_id']):raise Invalid('Invalid template Action slot.')
                continue
            action = self.studio.get(sensor['action_id'])
            if not action.supported:
                raise Invalid('Select a supported action.')
        by_id = {s['id']: s for s in config['sensors']}
        for sensor in config['sensors']:
            if 'rate' not in sensor:continue
            source = by_id.get(sensor['rate']['source'])
            if source is None or 'rate' in source or source['id'] == sensor['id']:
                raise Invalid('Rate source must be an Action sensor in this device; derived sources and cycles are not supported.')
        if not isinstance(config['functions'], list) or len(config['functions']) > 16:
            raise Invalid('Provide up to 16 functions.')
        ids = set()
        for function in config['functions']:
            if not isinstance(function, dict) or set(function) != {'id', 'name', 'action_id', 'function'}:
                raise Invalid('Provide the complete function definition.')
            if not identifier(function['id']) or function['id'] in ids or not name(function['name']):
                raise Invalid('Function IDs must be unique lowercase names; provide a function name.')
            ids.add(function['id'])
            if template:
                if not identifier(function['action_id']) or function['function'] != 'execute':
                    raise Invalid('Invalid template function or Action slot.')
                continue
            action = self.studio.get(function['action_id'])
            from .plugins import device_functions
            if not action.supported or function['function'] != 'execute' or 'execute' not in device_functions(action.plugin.metadata):
                raise Invalid('Select an action that supports the Execute function.')
        return config

    async def save(self, id, value):
        self.get(id)
        if id in self.editing:raise Invalid('Device configuration is being saved.')
        self.editing.add(id)
        try:return await self._save(id, value)
        finally:self.editing.discard(id)

    async def _save(self, id, value):
        if not isinstance(value, dict) or set(value) not in ({'config', 'activate'}, {'config', 'enabled'}):
            raise Invalid('Provide device configuration and its active state.')
        apply_settings = 'enabled' in value
        activate = value['enabled'] if apply_settings else value['activate']
        if type(activate) is not bool:
            raise Invalid('Provide device configuration and its active state.')
        async with self.studio.brokers.lock, AsyncExitStack() as locks:
            if self.busy(id):raise Invalid('Device is measuring. Wait for completion.')
            current = self.get(id)
            config = self.validate(value['config'])
            bindings = {}
            semantics_by_id = copy.deepcopy(current['sensor_identities'])
            if activate or apply_settings:
                if activate and not (config['sensors'] or config['functions']):raise Invalid('Add at least one sensor or function before enabling the device.')
                if config['publish'] and (activate or not current['active'] or not current['active']['publish']) and not self.studio.discovery.data['config']['enabled']:
                    raise Invalid('Enable central Home Assistant discovery in Settings before publishing devices.')
                if activate:
                    for action_id in sorted({s['action_id'] for s in config['sensors'] + config['functions'] if 'rate' not in s}):
                        await locks.enter_async_context(self.studio.get(action_id).lock)
                for sensor in config['sensors']:
                    old = semantics_by_id.get(sensor['id'])
                    semantics = [sensor['device_class'], sensor['unit'], sensor['cumulative']]
                    if 'rate' in sensor:semantics.append(sensor['rate'])
                    if old and old != semantics:raise Invalid('A sensor’s published metadata and counter behavior cannot change. Add a new sensor ID instead.')
                    if activate or config['publish']:semantics_by_id[sensor['id']] = semantics
                for action_id in sorted({s['action_id'] for s in config['sensors'] + config['functions'] if 'rate' not in s}):
                    if not activate:continue
                    action = self.studio.get(action_id)
                    if action.environment:action.environment.require()
                    action_config = await action.check_config(action.config['active'] if action.enabled else action.config['draft'])
                    from .actions import parse
                    _, error = await parse(json.dumps({'request_id':'device-validation','data':{}}), action.plugin)
                    if error:raise Invalid('Device actions must accept an empty input object; configure their source in Action settings.')
                    bindings[action_id] = {'config': action_config,
                        'plugin': copy.deepcopy(action.plugin.record) if action.environment else None}
            candidate = copy.deepcopy(self.data)
            item = candidate['items'][id]
            item['draft'] = config
            item['sensor_identities'] = semantics_by_id
            if activate or apply_settings:
                old_sensors = {s['id']: s for s in (current['active'] or {'sensors': []})['sensors']}
                changed = {s['id'] for s in config['sensors'] if old_sensors.get(s['id']) != s}
                # Labels and validation limits do not change the measurement's provenance.
                changed = {s['id'] for s in config['sensors'] if s['id'] in changed and s['id'] in old_sensors and
                    any(old_sensors.get(s['id'], {}).get(k) != s.get(k)
                        for k in ('action_id', 'reading', 'unit', 'cumulative', 'rate'))}
                for sensor in config['sensors']:
                    if sensor['id'] in changed or sensor.get('rate', {}).get('source') in changed:
                        item['values'].pop(sensor['id'], None)
            if activate:
                item.update(active=copy.deepcopy(config), enabled=True, bindings=bindings,
                            status='ready', next_run=time.time() + config['interval_minutes'] * 60 if config['sensors'] else None)
            elif apply_settings:
                item.update(active=copy.deepcopy(config), enabled=False, status='disabled', next_run=None)
            # Validate new discovery topics before committing device activation.
            previous = self.data
            self.data = candidate
            try:
                discovery = self.studio.discovery
                if discovery.data['config']['ha_status_topic'] in set(discovery.desired()) | set(discovery.command_routes()):
                    raise Invalid('Home Assistant status topic must not overlap device topics.')
                self.studio.brokers.check_topology()
            finally:self.data = previous
            self.commit(candidate)
        return self.public(id)

    def deactivate(self, id):
        if id in self.editing:raise Invalid('Device configuration is being saved.')
        if self.busy(id):raise Invalid('Device is measuring. Wait for completion.')
        self.get(id)
        candidate = copy.deepcopy(self.data)
        candidate['items'][id].update(enabled=False, status='disabled', next_run=None)
        self.commit(candidate)
        return self.public(id)

    def delete(self, value):
        if not isinstance(value, dict) or set(value) != {'id', 'confirmed'} or value['confirmed'] is not True:
            raise Invalid('Confirm device deletion.')
        id = value['id'];self.get(id)
        if id in self.editing:raise Invalid('Device configuration is being saved.')
        if self.busy(id):raise Invalid('Device is measuring. Wait for completion.')
        candidate = copy.deepcopy(self.data)
        candidate['deleted'].append(candidate['items'].pop(id))
        self.commit(candidate)
        return self.list()

    def invoke_function(self, id, value, *, remote=False):
        if not isinstance(value, dict) or set(value) != {'function_id', 'request_id'}:
            raise Invalid('Provide function ID and request ID.')
        request_id = value['request_id']
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or not request_id.strip() or any(ord(c) < 32 for c in request_id):
            raise Invalid('Invalid function request ID.')
        item = self.get(id)
        config = item['active']
        function = next((f for f in (config or {}).get('functions', []) if f['id'] == value['function_id']), None)
        if not function:raise Invalid('Device function not found.')
        if remote and not config['publish']:raise Invalid('Device function is not published.')
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        previous = item['function_requests'].get(request_id)
        if previous:
            if previous['digest'] != digest:raise Invalid('Function request ID was already used with a different payload.')
            return self.public(id)
        return self.start_read(id, function['action_id'], (request_id, digest, function))

    def start_read(self, id, action_id=None, request=None):
        item = self.get(id)
        if self.error:raise Invalid(self.error)
        if id in self.editing:raise Invalid('Device configuration is being saved.')
        if not item['enabled'] or not item['active']:raise Invalid('Activate the device before reading.')
        if self.busy(id):raise Invalid('Device is already measuring.')
        if action_id is None and not item['active']['sensors']:raise Invalid('Add sensors to read the whole device, or run a configured function.')
        candidate = copy.deepcopy(self.data)
        if request:
            request_id, digest, function = request
            candidate['items'][id]['function_requests'][request_id] = {
                'digest': digest, 'function': copy.deepcopy(function),
                'binding': copy.deepcopy(item['bindings'][action_id])}
            candidate['items'][id]['function_requests'] = dict(list(candidate['items'][id]['function_requests'].items())[-MAX_FUNCTION_REQUESTS:])
        candidate['items'][id].update(status='running', last_attempt=timestamp(),
            next_run=item['next_run'] if action_id else time.time() + item['active']['interval_minutes'] * 60 if item['active']['sensors'] else None)
        sources = {s['id']: s['action_id'] for s in item['active']['sensors'] if 'rate' not in s}
        for sensor in item['active']['sensors']:
            if 'rate' in sensor and (action_id is None or sources[sensor['rate']['source']] == action_id):
                candidate['items'][id]['values'].setdefault(sensor['id'], {}).update(
                    status='unavailable', error='Waiting for a new valid source measurement.')
        self.commit(candidate)  # Durable intent before any action, including restart interruption.
        self.logs[id] = []
        self.log(id, 'Measurement started.')
        self.tasks[id] = asyncio.create_task(self.measure(id, action_id))
        return self.public(id)

    async def measure(self, id, selected_action=None):
        item = copy.deepcopy(self.get(id))
        config = item['active']
        values = copy.deepcopy(item['values'])
        successes = 0
        action_successes = 0
        targets = {selected_action} if selected_action else {s['action_id'] for s in config['sensors'] if 'rate' not in s}
        from .test_progress import current
        token = current.set(lambda message: self.log(id, message))
        try:
            for action_id, binding in item['bindings'].items():
                if action_id not in targets:continue
                sensors = [s for s in config['sensors'] if s['action_id'] == action_id]
                try:
                    self.log(id, 'Waiting for action ' + action_id + '.')
                    action = self.studio.get(action_id)
                    if action.environment and action.plugin.record != binding['plugin']:
                        raise Invalid('Action source changed. Reactivate the device.')
                    data = {}
                    # Meter actions accept prior readings for their own rate/confirmation checks.
                    if 'previous' in action.plugin.metadata.get('input_schema', {}).get('properties', {}).get('data', {}).get('properties', {}):
                        data['previous'] = {s['reading']: {'value': values[s['id']]['value'],
                            'elapsed_seconds': max(.001, time.time() - values[s['id']]['measured_at'])}
                            for s in sensors if s['id'] in values and type(values[s['id']].get('value')) in (int, float)}
                    result = await action.internal_request('device-' + uuid.uuid4().hex, data, binding['config'])
                    if result.get('status') != 'success':raise Invalid('Action execution failed.')
                    self.log(id, 'Action finished. Validating sensor readings.')
                    output = result['data']
                    readings = output.get('readings') if isinstance(output, dict) else None
                    if sensors and not isinstance(readings, list):raise Invalid('Action must return a readings array.')
                    if not sensors and isinstance(output, dict) and output.get('status') == 'unreadable':raise Invalid('Action could not read its source.')
                    action_successes += 1
                    measured_at = time.time()
                    for sensor in sensors:
                        try:
                            matches = [r for r in readings if isinstance(r, dict) and r.get('id') == sensor['reading']]
                            if not matches:
                                message = output.get('message') if output.get('status') == 'unreadable' else None
                                raise Invalid(message.strip() if isinstance(message, str) and message.strip() else 'Named reading is missing.')
                            if len(matches) != 1:raise Invalid('Named reading is duplicated.')
                            reading = matches[0];value = reading.get('value')
                            numeric = type(value) in (int, float)
                            textual = isinstance(value, str)
                            if reading.get('status') != 'read' or not (numeric or textual):
                                reason = reading.get('reason') or reading.get('message')
                                raise Invalid(reason.strip() if isinstance(reason, str) and reason.strip() else 'Action did not return a valid number or text reading.')
                            if numeric and not math.isfinite(value):
                                raise Invalid('Action returned a non-finite number.')
                            if textual and (not value.strip() or len(value) > 255):
                                raise Invalid('Text readings must contain 1–255 characters and must not be blank.')
                            if textual and (sensor['unit'] or sensor['device_class'] or sensor['cumulative'] or sensor['max_change'] is not None):
                                raise Invalid('Text readings require an empty unit and HA device class, with counter and maximum-change checks disabled.')
                            previous = values.get(sensor['id'], {}).get('value')
                            if previous is not None and isinstance(previous, str) != textual:
                                raise Invalid('Reading changed between number and text. Use a new sensor ID to preserve Home Assistant history.')
                            if numeric:
                                if sensor['cumulative'] and (value < 0 or previous is not None and value < previous):
                                    raise Invalid('Counter reading decreased or is negative. Check the reading or configure a new sensor for a replacement counter.')
                                if previous is not None and sensor['max_change'] is not None and abs(value - previous) > sensor['max_change']:
                                    raise Invalid('Reading exceeds the maximum change.')
                            now = reading.get('measured_at', measured_at)
                            if type(now) not in (int, float) or not math.isfinite(now) or now < 0 or now > measured_at:
                                raise Invalid('Measurement time must be a finite Unix timestamp, not in the future.')
                            if values.get(sensor['id'], {}).get('measured_at', -1) >= now:
                                raise Invalid('Measurement time must be later than the last valid reading.')
                            values[sensor['id']] = {'value': value, 'measured_at': now, 'timestamp': timestamp(now), 'status': 'ready', 'error': None}
                            successes += 1
                            self.log(id, sensor['id'] + ': Reading accepted.')
                        except Invalid as exc:
                            values.setdefault(sensor['id'], {}).update(status='error', error=str(exc))
                            self.log(id, sensor['id'] + ': ' + str(exc))
                except Exception:
                    self.log(id, 'Action failed or returned an unsupported reading. Check its configuration.')
                    for sensor in sensors:values.setdefault(sensor['id'], {}).update(status='error', error='Action failed or returned an unsupported reading. Check its configuration.')
                for sensor in config['sensors']:
                    if 'rate' not in sensor or sensor['rate']['source'] not in {s['id'] for s in sensors}:continue
                    try:
                        rate = sensor['rate']
                        source = values.get(rate['source'], {})
                        previous = item['values'].get(rate['source'], {})
                        if source.get('status') != 'ready' or type(source.get('value')) not in (int, float):
                            raise Invalid('Source sensor has no valid numeric measurement.')
                        if type(previous.get('value')) not in (int, float):
                            raise Invalid('Waiting for two valid source measurements.')
                        elapsed = source['measured_at'] - previous['measured_at']
                        if elapsed <= 0:raise Invalid('Source measurement time must increase.')
                        value = ((source['value'] - previous['value']) / elapsed) * rate['factor'] * {'second': 1, 'minute': 60, 'hour': 3600}[rate['time_base']]
                        if not math.isfinite(value):raise Invalid('Calculated rate is not finite.')
                        old = values.get(sensor['id'], {}).get('value')
                        if old is not None and sensor['max_change'] is not None and abs(value - old) > sensor['max_change']:
                            raise Invalid('Calculated rate exceeds the maximum change.')
                        values[sensor['id']] = {'value': value, 'measured_at': source['measured_at'],
                            'timestamp': source['timestamp'], 'status': 'ready', 'error': None}
                        successes += 1
                        self.log(id, sensor['id'] + ': Rate calculated.')
                    except (Invalid, OverflowError) as exc:
                        if isinstance(exc, OverflowError):exc = Invalid('Calculated rate is not finite.')
                        values.setdefault(sensor['id'], {}).update(status='unavailable', error=str(exc))
                        self.log(id, sensor['id'] + ': ' + str(exc))
                # Preserve completed independent measurements if a later action is interrupted.
                candidate = copy.deepcopy(self.data)
                candidate['items'][id]['values'] = copy.deepcopy(values)
                self.commit(candidate)
            candidate = copy.deepcopy(self.data)
            sources = {s['id']: s['action_id'] for s in config['sensors'] if 'rate' not in s}
            expected = sum((sources[s['rate']['source']] if 'rate' in s else s['action_id']) in targets for s in config['sensors'])
            candidate['items'][id].update(status='ready' if successes == expected and action_successes == len(targets) else 'partial' if successes else 'error',
                next_run=item['next_run'] if selected_action else time.time() + config['interval_minutes'] * 60 if config['sensors'] else None)
            self.commit(candidate)
            self.log(id, 'Measurement finished: ' + self.get(id)['status'] + '.')
        except asyncio.CancelledError:
            self.log(id, 'Measurement interrupted; it will not be replayed.')
            raise  # Persisted running intent is recovered as interrupted, never replayed.
        except Exception:
            self.error = 'Device persistence failed. Automatic measurements are paused; restart after resolving storage.'
            self.log(id, self.error)
        finally:
            current.reset(token)

    async def run(self):
        while not self.stopping:
            if not self.error:
                try:
                    for id, item in list(self.data['items'].items()):
                        if item['enabled'] and item['next_run'] is not None and item['next_run'] <= time.time() and not self.busy(id) and id not in self.editing:
                            self.start_read(id)
                except Exception:self.error = 'Device scheduler failed. Automatic measurements are paused; check storage and restart.'
            await asyncio.sleep(.5)

    async def close(self):
        self.stopping = True
        if self.task:await self.task
        for task in self.tasks.values():
            if not task.done():task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
