import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from devicecourt.store import connect, replay, run


class ComputeSliceTest(unittest.TestCase):
    def test_advertised_shape_and_existing_slice_jointly_limit_placement(self):
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'compute_slices.json'
        steps = json.loads(fixture.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'board.sqlite'
            with closing(connect(path)) as db:
                result = [run(db, step['command'], step['input']) for step in steps]
            self.assertEqual(result[6:9], [{'decision': 'slice_reserved', 'lanes': [1]}, {'decision': 'slice_reserved', 'lanes': [2,3]}, {'decision': 'no_slice_footprint'}])
            self.assertEqual(result[-1], {'decision': 'slice_reserved', 'lanes': [4,5]})
            self.assertTrue(replay(path)['verified'])
