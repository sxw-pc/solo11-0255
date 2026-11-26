import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from devicecourt.store import connect, replay, rows, run


class FabricTest(unittest.TestCase):
    def test_inventory_isolation_stops_packets_without_freeing_the_cable_port(self):
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'fabric.json'
        commands = json.loads(fixture.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fabric.sqlite'
            with closing(connect(path)) as db:
                received = []
                for command in commands:
                    if command['input']['event_id'] == 'unplug-a':
                        plug = dict(commands[-1]['input'], event_id='port-not-yet-free')
                        self.assertEqual(run(db, 'fabric', plug)['decision'], 'fabric_port_occupied')
                    result = run(db, command['command'], command['input'])
                    received.append(result['decision'])
                self.assertEqual(received[-5:], ['fabric_exchange_delivered', 'isolated',
                    'fabric_exchange_blocked', 'fabric_cable_disconnected', 'fabric_cable_connected'])
                self.assertEqual(rows(db, 'SELECT id FROM fabric_exchanges'), [{'id': 'send-before'}])
            self.assertTrue(replay(path)['verified'])
