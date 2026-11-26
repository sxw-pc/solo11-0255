"""Physical compute-slice layout and exclusive assignment for offline boards."""
import json

from .store import one, rows

INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS slice_layouts(
    device TEXT NOT NULL, epoch INTEGER NOT NULL, revision INTEGER NOT NULL,
    profiles TEXT NOT NULL, PRIMARY KEY(device,epoch,revision)
);
"""
ALLOCATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS slice_leases(
    id TEXT PRIMARY KEY, parent_allocation TEXT NOT NULL, device TEXT NOT NULL,
    epoch INTEGER NOT NULL, layout_revision INTEGER NOT NULL, profile TEXT NOT NULL,
    lanes TEXT NOT NULL, job TEXT NOT NULL, state TEXT NOT NULL
);
"""


def publish(db, command):
    dev = one(db, "SELECT d.*,p.epoch AS current_epoch FROM devices d JOIN plugins p ON p.id=d.plugin WHERE d.id=?", (command['device'],))
    if not dev or dev['status'] != 'healthy' or dev['epoch'] != command['epoch'] or dev['current_epoch'] != command['epoch']:
        return {'decision': 'slice_board_unavailable'}
    revision, profiles = command['revision'], command['profiles']
    if type(revision) is not int or revision < 1 or not isinstance(profiles, dict) or not profiles:
        return {'decision': 'invalid_slice_layout'}
    for placements in profiles.values():
        if not isinstance(placements, list) or not placements or any(not isinstance(lanes, list) or not lanes or any(type(lane) is not int or lane not in range(8) for lane in lanes) or len(lanes) != len(set(lanes)) for lanes in placements):
            return {'decision': 'invalid_slice_layout'}
    prior = one(db, 'SELECT MAX(revision) AS revision FROM slice_layouts WHERE device=? AND epoch=?', (dev['id'], dev['epoch']))['revision']
    if prior is not None and revision <= prior:
        return {'decision': 'stale_slice_layout'}
    db.execute('INSERT INTO slice_layouts VALUES(?,?,?,?)', (dev['id'], dev['epoch'], revision, json.dumps(profiles, sort_keys=True)))
    return {'decision': 'slice_layout_published', 'revision': revision}


def reserve(db, command):
    lease = one(db, "SELECT a.*,d.epoch AS current_epoch,d.status AS device_status FROM allocations a JOIN devices d ON d.id=a.device WHERE a.id=?", (command['allocation'],))
    if not lease or lease['status'] not in ('reserved','running') or lease['device_status'] != 'healthy' or lease['device_epoch'] != lease['current_epoch']:
        return {'decision': 'slice_parent_unavailable'}
    layout = one(db, 'SELECT * FROM slice_layouts WHERE device=? AND epoch=? ORDER BY revision DESC LIMIT 1', (lease['device'], lease['device_epoch']))
    if not layout or command['revision'] != layout['revision']:
        return {'decision': 'current_slice_layout_required'}
    active = rows(db, "SELECT lanes FROM slice_leases WHERE device=? AND state='active'", (lease['device'],))
    occupied = {lane for row in active for lane in json.loads(row['lanes'])}
    shapes = json.loads(layout['profiles']).get(command['profile'], [])
    options = [sorted(shape) for shape in shapes if occupied.isdisjoint(shape)]
    if not options:
        return {'decision': 'no_slice_footprint'}
    selected = min(options)
    db.execute('INSERT INTO slice_leases VALUES(?,?,?,?,?,?,?,?,?)', (command['id'], lease['id'], lease['device'], lease['device_epoch'], layout['revision'], command['profile'], json.dumps(selected), command['job'], 'active'))
    return {'decision': 'slice_reserved', 'lanes': selected}
