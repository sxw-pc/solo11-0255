import json
import tempfile
import unittest
from pathlib import Path

from devicecourt.store import connect, one, replay, rows, run

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / 'fixtures' / name).read_text(encoding='utf-8'))


class SliceMaintenanceContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'board.sqlite'
        self.db = connect(str(self.path))
        self.addCleanup(self.db.close)

    def run_steps(self, name):
        return [run(self.db, step['command'], step['input']) for step in load(name)]

    def occupied(self):
        held = rows(self.db, "SELECT lanes FROM slice_leases WHERE state IN ('active','prepared')")
        return {lane for row in held for lane in json.loads(row['lanes'])}

    def running(self):
        return {row['job'] for row in rows(self.db, "SELECT job FROM slice_jobs WHERE status='running'")}

    def active_lanes(self, job):
        row = one(self.db, "SELECT lanes FROM slice_leases WHERE job=? AND state='active'", (job,))
        return json.loads(row['lanes']) if row else None

    def water_of(self, job):
        return one(self.db, 'SELECT water FROM slice_jobs WHERE job=?', (job,))['water']

    def test_continuous_delivery_contract(self):
        out = self.run_steps('slice_continuous.json')
        self.assertEqual(out[6], {'decision': 'slice_reserved', 'lanes': [1]})
        self.assertEqual(out[7], {'decision': 'slice_reserved', 'lanes': [2, 3]})
        self.assertEqual(out[8]['decision'], 'no_slice_footprint')
        # An ordinary swap cannot hold two overlapping bindings at once, even
        # though [2,5] collides with no other job's coordinates.
        self.assertEqual(out[10]['decision'], 'no_slice_sequence')
        self.assertEqual(out[11], {'decision': 'slice_prepared', 'lanes': [4, 6]})
        self.assertEqual(out[12], {'decision': 'slice_water_recorded', 'kind': 'executed', 'water': 5})
        self.assertEqual(out[13], {'decision': 'slice_water_recorded', 'kind': 'handover', 'water': 5})
        self.assertEqual(out[14], {'decision': 'slice_committed', 'lanes': [4, 6], 'exited': 'lw'})
        self.assertEqual(out[15]['decision'], 'slice_reclaimed')
        self.assertEqual(out[16], {'decision': 'slice_prepared', 'lanes': [2, 5]})
        self.assertEqual(out[19], {'decision': 'slice_committed', 'lanes': [2, 5], 'exited': 'lw-p1'})
        self.assertEqual(out[20]['decision'], 'slice_reclaimed')
        self.assertEqual(out[22], {'decision': 'slice_prepared', 'lanes': [3]})
        self.assertEqual(out[25], {'decision': 'slice_committed', 'lanes': [3], 'exited': 'ls'})
        self.assertEqual(out[27], {'decision': 'slice_reserved', 'lanes': [0, 1]})
        # Contract: W, S and X all run; in-flight water levels survived every move.
        self.assertEqual(self.running(), {'S', 'W', 'X'})
        self.assertEqual(self.active_lanes('W'), [2, 5])
        self.assertEqual(self.active_lanes('S'), [3])
        self.assertEqual(self.active_lanes('X'), [0, 1])
        self.assertEqual(self.water_of('W'), 6)
        self.assertEqual(self.water_of('S'), 3)
        # The temporary [4,6] staging shape was handed back after rearrangement.
        self.assertEqual(self.occupied(), {0, 1, 2, 3, 5})
        self.assertFalse(rows(self.db, "SELECT 1 FROM slice_leases WHERE state='prepared'"))
        self.assertTrue(replay(str(self.path))['verified'])

    def test_original_residence_contract(self):
        out = self.run_steps('slice_residence.json')
        self.assertEqual(out[6], {'decision': 'slice_reserved', 'lanes': [1]})
        self.assertEqual(out[7], {'decision': 'slice_reserved', 'lanes': [2, 3]})
        self.assertEqual(out[8]['decision'], 'no_slice_footprint')
        self.assertEqual(out[9], {'decision': 'slice_water_recorded', 'kind': 'executed', 'water': 4})
        # A running slice may not drop its binding without a stop authorization.
        self.assertEqual(out[10]['decision'], 'slice_pause_unproven')
        self.assertEqual(out[11], {'decision': 'slice_paused', 'water': 4})
        self.assertEqual(out[12]['decision'], 'slice_reclaimed')
        # A paused job must not be reported as still executing.
        self.assertEqual(out[13]['decision'], 'slice_lease_not_executable')
        self.assertEqual(out[14], {'decision': 'slice_reserved', 'lanes': [0, 1]})
        # Contract: W keeps its original residence, X joins, S leaves a recovery right.
        self.assertEqual(self.running(), {'W', 'X'})
        self.assertEqual(self.active_lanes('W'), [2, 3])
        self.assertEqual(self.active_lanes('X'), [0, 1])
        self.assertIsNone(self.active_lanes('S'))
        right = one(self.db, "SELECT * FROM slice_recovery_rights WHERE job='S'")
        self.assertEqual((right['water'], right['status'], right['profile']), (4, 'open', 'small'))
        self.assertEqual(one(self.db, "SELECT status FROM slice_jobs WHERE job='S'")['status'], 'paused')
        self.assertTrue(replay(str(self.path))['verified'])

    def test_late_handover_receipt_then_withdrawal_waits(self):
        steps = load('slice_late_receipt.json')
        out = []
        for index, step in enumerate(steps):
            out.append(run(self.db, step['command'], step['input']))
            if index == 12:
                # Prepare succeeded but the handover receipt is late: the swap is
                # refused while W keeps its executable binding and water level,
                # and the staging binding really holds units 4 and 6.
                self.assertEqual(out[-1]['decision'], 'slice_water_unproven')
                self.assertEqual(self.occupied(), {1, 2, 3, 4, 6})
                self.assertEqual(self.active_lanes('W'), [2, 3])
                self.assertEqual(self.water_of('W'), 5)
        self.assertEqual(out[9]['decision'], 'no_slice_footprint')
        self.assertEqual(out[10], {'decision': 'slice_prepared', 'lanes': [4, 6]})
        self.assertEqual(out[13]['decision'], 'slice_prepare_pending')
        # The late receipt is recorded as a fact, but after the discovery court
        # withdrew [4,6] the allocation court must not commit the stale action.
        self.assertEqual(out[15], {'decision': 'slice_water_recorded', 'kind': 'handover', 'water': 5})
        self.assertEqual(out[16]['decision'], 'slice_layout_superseded')
        self.assertEqual(out[17]['decision'], 'slice_aborted')
        # Continuous delivery can only wait: W and S run, X was never placed.
        self.assertEqual(self.running(), {'S', 'W'})
        self.assertEqual(self.active_lanes('W'), [2, 3])
        self.assertEqual(self.water_of('W'), 5)
        self.assertEqual(self.occupied(), {1, 2, 3})
        self.assertTrue(replay(str(self.path))['verified'])
