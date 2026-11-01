"""MQTT v5 transport for action requests and optional central discovery."""
import asyncio
import json
import os
import threading
import time
from urllib.request import Request, urlopen
from .domain import Invalid


async def run_transport(function, *args, **kwargs):
    """Drain a blocking transport call before allowing cancellation/cleanup.

    Cancelling to_thread's awaiter does not stop its worker. Shield the worker
    and wait even on repeated cancellation so close cannot race connect/publish.
    """
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # Retrieve a worker failure; cancellation remains primary.
        raise


def credentials(settings):
    conf = settings['mqtt']
    if conf['mode'] == 'manual':
        if not conf['host']:
            raise Invalid('MQTT host is missing.')
        return conf
    token = os.getenv('SUPERVISOR_TOKEN')
    if not token:
        raise Invalid('Supervisor MQTT is only available inside the HA app. Configure it manually for local use.')
    request = Request('http://supervisor/services/mqtt', headers={'Authorization': 'Bearer ' + token})
    with urlopen(request, timeout=10) as response:
        data = json.load(response)['data']
    return {**data, 'tls': bool(data.get('ssl', False))}


class MQTT:
    def __init__(self, identity, trigger, subscribe=True, report=None, commands=None):
        self.commands = commands or {}
        self.report=report or (lambda stage,message:None)
        self.connection_error=None
        self.subscribe=subscribe
        self.availability_topic=identity.get("availability_topic")
        self.accept_retained=identity.get("accept_retained",False)
        self.request_topic=identity.get('request_topic',identity.get('command_prefix',identity['prefix'])+'/request')
        self.prefix = identity['prefix']
        self.device_id = identity['device_id']
        self.command_prefix = identity.get('command_prefix', self.prefix)
        self.device_name = identity.get('name', 'Agent')
        self.trigger = trigger
        self.client = None
        self.ready = threading.Event()
        self.needs_discovery = True
        self.publish_errors={}

    def connect(self, settings):
        import paho.mqtt.client as mqtt
        self.close()
        self.connection_error=None
        c = credentials(settings)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.prefix, protocol=mqtt.MQTTv5)
        pending=set();failure=threading.Event()
        def connected(client, userdata, flags, reason, props):
            self.ready.clear();pending.clear();failure.clear()
            if reason != 0:
                self.connection_error={132:'unsupported_protocol',133:'invalid_client_id',134:'bad_credentials',135:'not_authorized',136:'server_unavailable',137:'server_busy',138:'banned'}.get(reason.value,'connection_rejected')
                failure.set();return
            topics=[(self.request_topic,mqtt.SubscribeOptions(qos=1,retainAsPublished=True))] if self.subscribe else []
            topics += [(topic, mqtt.SubscribeOptions(qos=1, retainAsPublished=True)) for topic in self.commands]
            if topics:
                rc,mid=client.subscribe(topics)
                if rc:failure.set()
                else:pending.add(mid)
            else:self.ready.set()
            self.needs_discovery=True
        def subscribed(client, userdata, mid, reasons, props):
            if any(reason.is_failure for reason in reasons):failure.set();self.ready.clear();return
            pending.discard(mid)
            if not pending and not failure.is_set():self.ready.set()
        def message(client, userdata, msg):
            if self.subscribe and msg.topic == self.request_topic and (self.accept_retained or not msg.retain) and len(msg.payload) <= 1048576:
                self.trigger(msg.payload)
            if msg.topic in self.commands and not msg.retain and len(msg.payload) <= 1024:
                self.commands[msg.topic](msg.payload)
        client.on_publish=lambda client,userdata,mid,reason,props:self.publish_errors.__setitem__(mid,reason.is_failure)
        client.on_subscribe = subscribed
        client.on_connect = connected
        client.on_disconnect = lambda *args: self.ready.clear()
        client.on_message = message
        if self.availability_topic:client.will_set(self.availability_topic, 'offline', qos=1, retain=True)
        client.username_pw_set(c.get('username', ''), c.get('password', ''))
        if c.get('tls'):
            client.tls_set()
        self.client = client
        client.connect_timeout=8
        self.report('transport', 'Opening TCP connection'+(' and verifying TLS certificate.' if c.get('tls') else ' (TLS disabled).'))
        client.connect(c['host'], int(c['port']), 60)
        self.report('transport_ok', 'TCP connection established'+('; TLS certificate verified.' if c.get('tls') else '.'))
        self.report('mqtt', 'Waiting for MQTT v5 connection acknowledgement.')
        client.loop_start()
        deadline=time.monotonic()+12
        while not self.ready.wait(.05) and not failure.is_set() and time.monotonic()<deadline:pass
        if not self.ready.is_set():
            self.close()
            raise Invalid('MQTT connection or subscription failed. Check connection settings and topic permissions.')

    def publish(self, suffix, value, absolute=False, retain=True):
        if not self.ready.is_set():
            raise Invalid('MQTT is not connected.')
        topic = suffix if absolute else self.prefix + '/' + suffix
        payload = value if isinstance(value, (str, bytes)) else json.dumps(value, ensure_ascii=False)
        result = self.client.publish(topic, payload, qos=1, retain=retain)
        result.wait_for_publish(timeout=10)
        if not result.is_published() or self.publish_errors.pop(result.mid,False):
            raise Invalid('MQTT publication was not acknowledged.')

    def close(self):
        if self.client:
            if self.availability_topic and self.ready.is_set():
                try:
                    self.publish(self.availability_topic, 'offline', absolute=True)
                except Exception:
                    pass
            self.client.disconnect()
            self.client.loop_stop()
        self.client = None
        self.ready.clear()
