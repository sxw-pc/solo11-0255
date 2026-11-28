"""Physical compute-slice layout, exclusive assignment and live rearrangement."""
import json

from .store import one, rows

INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS slice_layouts(
    device TEXT NOT NULL, epoch INTEGER NOT NULL, revision INTEGER NOT NULL,
    profiles TEXT NOT NULL, temporary TEXT NOT NULL, PRIMARY KEY(device,epoch,revision)
);
"""
ALLOCATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS slice_leases(
    id TEXT PRIMARY KEY, parent_allocation TEXT NOT NULL, device TEXT NOT NULL,
    epoch INTEGER NOT NULL, layout_revision INTEGER NOT NULL, profile TEXT NOT NULL,
    lanes TEXT NOT NULL, job TEXT NOT NULL, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slice_jobs(
    job TEXT NOT NULL, device TEXT NOT NULL, epoch INTEGER NOT NULL,
    profile TEXT NOT NULL, water INTEGER NOT NULL, status TEXT NOT NULL,
    PRIMARY KEY(device,job)
);
CREATE TABLE IF NOT EXISTS slice_receipts(
    id TEXT PRIMARY KEY, lease TEXT NOT NULL, job TEXT NOT NULL,
    kind TEXT NOT NULL, water INTEGER NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slice_recovery_rights(
    job TEXT NOT NULL, device TEXT NOT NULL, epoch INTEGER NOT NULL,
    profile TEXT NOT NULL, water INTEGER NOT NULL, status TEXT NOT NULL,
    PRIMARY KEY(device,job)
);
"""


def _valid_shapes(shapes):
    return isinstance(shapes, list) and bool(shapes) and all(
        isinstance(lanes, list) and lanes
        and all(type(lane) is int and lane in range(8) for lane in lanes)
        and len(lanes) == len(set(lanes)) for lanes in shapes)


def _parent(db, allocation):
    return one(db, "SELECT a.*,d.epoch AS current_epoch,d.status AS device_status FROM allocations a JOIN devices d ON d.id=a.device WHERE a.id=?", (allocation,))


def _parent_available(parent):
    return bool(parent) and parent['status'] in ('reserved', 'running') and parent['device_status'] == 'healthy' and parent['device_epoch'] == parent['current_epoch']


def _layout(db, device, epoch):
    return one(db, 'SELECT * FROM slice_layouts WHERE device=? AND epoch=? ORDER BY revision DESC LIMIT 1', (device, epoch))


def _occupied(db, device):
    # Prepared staging bindings hold real compute units exactly like active ones.
    held = rows(db, "SELECT lanes FROM slice_leases WHERE device=? AND state IN ('active','prepared')", (device,))
    return {lane for row in held for lane in json.loads(row['lanes'])}


def _shapes(layout, profile, include_temporary):
    shapes = [sorted(shape) for shape in json.loads(layout['profiles']).get(profile, [])]
    if include_temporary:
        shapes += [sorted(shape) for shape in json.loads(layout['temporary']).get(profile, [])]
    return shapes


def _job(db, device, job):
    return one(db, 'SELECT * FROM slice_jobs WHERE device=? AND job=?', (device, job))


def _lease(db, device, job, state):
    return one(db, 'SELECT * FROM slice_leases WHERE device=? AND job=? AND state=?', (device, job, state))


def publish(db, command):
    dev = one(db, "SELECT d.*,p.epoch AS current_epoch FROM devices d JOIN plugins p ON p.id=d.plugin WHERE d.id=?", (command['device'],))
    if not dev or dev['status'] != 'healthy' or dev['epoch'] != command['epoch'] or dev['current_epoch'] != command['epoch']:
        return {'decision': 'slice_board_unavailable'}
    revision, profiles, temporary = command['revision'], command['profiles'], command.get('temporary', {})
    if type(revision) is not int or revision < 1 or not isinstance(profiles, dict) or not profiles or not isinstance(temporary, dict):
        return {'decision': 'invalid_slice_layout'}
    if any(not _valid_shapes(placements) for placements in list(profiles.values()) + list(temporary.values())):
        return {'decision': 'invalid_slice_layout'}
    prior = one(db, 'SELECT MAX(revision) AS revision FROM slice_layouts WHERE device=? AND epoch=?', (dev['id'], dev['epoch']))['revision']
    if prior is not None and revision <= prior:
        return {'decision': 'stale_slice_layout'}
    db.execute('INSERT INTO slice_layouts VALUES(?,?,?,?,?)', (dev['id'], dev['epoch'], revision, json.dumps(profiles, sort_keys=True), json.dumps(temporary, sort_keys=True)))
    return {'decision': 'slice_layout_published', 'revision': revision}


def reserve(db, command):
    parent = _parent(db, command['allocation'])
    if not _parent_available(parent):
        return {'decision': 'slice_parent_unavailable'}
    layout = _layout(db, parent['device'], parent['device_epoch'])
    if not layout or command['revision'] != layout['revision']:
        return {'decision': 'current_slice_layout_required'}
    if _job(db, parent['device'], command['job']):
        return {'decision': 'slice_job_conflict'}
    occupied = _occupied(db, parent['device'])
    # A first reservation is a final service position: temporary shapes never apply.
    options = [shape for shape in _shapes(layout, command['profile'], False) if occupied.isdisjoint(shape)]
    if not options:
        return {'decision': 'no_slice_footprint'}
    selected = min(options)
    db.execute('INSERT INTO slice_leases VALUES(?,?,?,?,?,?,?,?,?)', (command['id'], parent['id'], parent['device'], parent['device_epoch'], layout['revision'], command['profile'], json.dumps(selected), command['job'], 'active'))
    db.execute('INSERT INTO slice_jobs VALUES(?,?,?,?,?,?)', (command['job'], parent['device'], parent['device_epoch'], command['profile'], 0, 'running'))
    return {'decision': 'slice_reserved', 'lanes': selected}


def prepare(db, command):
    job = _job(db, command['device'], command['job'])
    if not job:
        return {'decision': 'slice_job_unknown'}
    if job['status'] != 'running':
        return {'decision': 'slice_job_not_running'}
    current = _lease(db, job['device'], job['job'], 'active')
    if not _parent_available(_parent(db, current['parent_allocation'])):
        return {'decision': 'slice_parent_unavailable'}
    layout = _layout(db, job['device'], job['epoch'])
    if not layout or command['revision'] != layout['revision']:
        return {'decision': 'current_slice_layout_required'}
    if _lease(db, job['device'], job['job'], 'prepared'):
        return {'decision': 'slice_prepare_pending'}
    shapes = _shapes(layout, job['profile'], True)
    # The job's own active binding still occupies its lanes, so a target overlapping
    # it has no legal actual sequence for one ordinary swap and is refused here.
    occupied = _occupied(db, job['device'])
    if 'lanes' in command:
        raw = command['lanes']
        if not isinstance(raw, list) or not raw or any(type(lane) is not int for lane in raw):
            return {'decision': 'invalid_slice_shape'}
        selected = sorted(set(raw))
        if selected not in shapes:
            return {'decision': 'slice_shape_not_advertised'}
        if not occupied.isdisjoint(selected):
            return {'decision': 'no_slice_sequence'}
    else:
        options = [shape for shape in shapes if occupied.isdisjoint(shape)]
        if not options:
            return {'decision': 'no_slice_sequence'}
        selected = min(options)
    db.execute('INSERT INTO slice_leases VALUES(?,?,?,?,?,?,?,?,?)', (command['id'], current['parent_allocation'], job['device'], job['epoch'], layout['revision'], job['profile'], json.dumps(selected), job['job'], 'prepared'))
    return {'decision': 'slice_prepared', 'lanes': selected}


def water(db, command):
    lease = one(db, 'SELECT * FROM slice_leases WHERE id=?', (command['lease'],))
    if not lease:
        return {'decision': 'slice_lease_unknown'}
    if lease['state'] not in ('active', 'prepared'):
        return {'decision': 'slice_lease_not_executable'}
    level = command['water']
    if type(level) is not int or level < 0:
        return {'decision': 'invalid_slice_water'}
    if not _parent_available(_parent(db, lease['parent_allocation'])):
        return {'decision': 'slice_parent_unavailable'}
    job = _job(db, lease['device'], lease['job'])
    kind = 'executed' if lease['state'] == 'active' else 'handover'
    if kind == 'executed':
        if level < job['water']:
            return {'decision': 'stale_slice_water'}
        db.execute('UPDATE slice_jobs SET water=? WHERE device=? AND job=?', (level, lease['device'], lease['job']))
    db.execute('INSERT INTO slice_receipts VALUES(?,?,?,?,?,?)', (command['id'], lease['id'], lease['job'], kind, level, 'accepted'))
    return {'decision': 'slice_water_recorded', 'kind': kind, 'water': level}


def commit(db, command):
    lease = one(db, 'SELECT * FROM slice_leases WHERE id=?', (command['lease'],))
    if not lease or lease['state'] != 'prepared':
        return {'decision': 'slice_prepare_required'}
    job = _job(db, lease['device'], lease['job'])
    if not job or job['status'] != 'running':
        return {'decision': 'slice_job_not_running'}
    if not _parent_available(_parent(db, lease['parent_allocation'])):
        return {'decision': 'slice_parent_unavailable'}
    layout = _layout(db, lease['device'], lease['epoch'])
    if not layout or layout['revision'] != lease['layout_revision']:
        return {'decision': 'slice_layout_superseded'}
    caught = one(db, "SELECT MAX(water) AS water FROM slice_receipts WHERE lease=? AND kind='handover'", (lease['id'],))['water']
    if caught is None or caught < job['water']:
        return {'decision': 'slice_water_unproven'}
    current = _lease(db, lease['device'], lease['job'], 'active')
    db.execute("UPDATE slice_leases SET state='exited' WHERE id=?", (current['id'],))
    db.execute("UPDATE slice_leases SET state='active' WHERE id=?", (lease['id'],))
    return {'decision': 'slice_committed', 'lanes': json.loads(lease['lanes']), 'exited': current['id']}


def reclaim(db, command):
    lease = one(db, 'SELECT * FROM slice_leases WHERE id=?', (command['lease'],))
    if not lease or lease['state'] != 'exited':
        return {'decision': 'slice_reclaim_denied'}
    db.execute("UPDATE slice_leases SET state='reclaimed' WHERE id=?", (lease['id'],))
    return {'decision': 'slice_reclaimed'}


def abort(db, command):
    lease = one(db, 'SELECT * FROM slice_leases WHERE id=?', (command['lease'],))
    if not lease or lease['state'] != 'prepared':
        return {'decision': 'slice_abort_denied'}
    db.execute("UPDATE slice_leases SET state='reclaimed' WHERE id=?", (lease['id'],))
    return {'decision': 'slice_aborted'}


def pause(db, command):
    job = _job(db, command['device'], command['job'])
    if not job:
        return {'decision': 'slice_job_unknown'}
    if job['status'] != 'running':
        return {'decision': 'slice_job_not_running'}
    if not command.get('pause_receipt'):
        return {'decision': 'slice_pause_unproven'}
    current = _lease(db, job['device'], job['job'], 'active')
    if not _parent_available(_parent(db, current['parent_allocation'])):
        return {'decision': 'slice_parent_unavailable'}
    if _lease(db, job['device'], job['job'], 'prepared'):
        return {'decision': 'slice_prepare_pending'}
    db.execute("UPDATE slice_leases SET state='exited' WHERE id=?", (current['id'],))
    db.execute("UPDATE slice_jobs SET status='paused' WHERE device=? AND job=?", (job['device'], job['job']))
    db.execute('INSERT INTO slice_recovery_rights VALUES(?,?,?,?,?,?)', (job['job'], job['device'], job['epoch'], job['profile'], job['water'], 'open'))
    db.execute('INSERT INTO slice_receipts VALUES(?,?,?,?,?,?)', (command['id'], current['id'], job['job'], 'pause', job['water'], 'accepted'))
    return {'decision': 'slice_paused', 'water': job['water']}
