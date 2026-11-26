from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

from devicecourt.store import connect, replay, rows, run


class DevicePacketTest(unittest.TestCase):
    def test_launch_unlocks_the_real_decoder_for_the_same_bytes(self):
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'command_packets.json'
        original = json.loads(fixture.read_text(encoding='utf-8'))
        for family in ('E','W'):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'packets.sqlite'
                with closing(connect(path)) as db:
                    commands = json.loads(json.dumps(original))
                    commands[5]['input']['family'] = family
                    results = [run(db,x['command'],x['input']) for x in commands]
                    self.assertEqual(results[-3]['decision'], 'packet_target_unavailable')
                    self.assertEqual(results[-2]['decision'], 'device_delivered')
                    self.assertEqual(results[-1], {'decision':'packet_executed','width':6,'stride':9,'work':54})
                    self.assertEqual(rows(db,'SELECT family,packet FROM packet_runs'),[{'family':family,'packet':'010309'}])
                self.assertTrue(replay(path)['verified'])
