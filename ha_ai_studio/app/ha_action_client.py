"""Action-side stdlib client. Contains no Home Assistant credentials."""
import json
from pathlib import Path
import socket

class HomeAssistantClient:
    def __init__(self,connection):self.connection=connection
    def _call(self,operation,entity_id,**details):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(30);sock.connect(self.connection['socket'])
            sock.sendall(json.dumps({'token':self.connection['token'],'operation':operation,'entity_id':entity_id,**details}).encode()+b'\n')
            with sock.makefile('rb') as stream:raw=stream.readline(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ValueError('Home Assistant response exceeded its limit.')
        response=json.loads(raw)
        if 'error' in response:raise ValueError(response['error'])
        return response['result']
    def state(self,entity_id):return self._call('state',entity_id)
    def history(self,entity_id,hours=24):return self._call('history',entity_id,hours=hours)
    def capture(self,entity_id):return Path(self._call('capture',entity_id)['path']).read_bytes()
    def light_on(self,entity_id):return self._call('light_on',entity_id)
    def restore(self,entity_id):return self._call('restore',entity_id)

    def report_wait(self,entity_id,phase,seconds):return self._call('wait',entity_id,phase=phase,seconds=seconds)
