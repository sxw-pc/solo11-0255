"""Manage and exercise direct links between synthetic device ports."""
from .store import one, rows

SCHEMA = """
CREATE TABLE IF NOT EXISTS fabric_cables(
    id TEXT PRIMARY KEY, left_uid TEXT NOT NULL, left_port TEXT NOT NULL,
    left_epoch INTEGER NOT NULL, right_uid TEXT NOT NULL, right_port TEXT NOT NULL,
    right_epoch INTEGER NOT NULL, connected INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fabric_exchanges(
    id TEXT PRIMARY KEY, source TEXT NOT NULL, destination TEXT NOT NULL,
    cable TEXT NOT NULL, payload TEXT NOT NULL
);
"""


def active_device(db, uid, epoch):
    return one(db, """
        SELECT d.id FROM devices d JOIN plugins p ON p.id=d.plugin
        WHERE d.id=? AND d.epoch=? AND p.epoch=d.epoch AND d.status='healthy'
    """, (uid, epoch)) is not None


def connect_cable(db, command):
    left, right = (command[side] for side in ('left', 'right'))
    if left['device'] == right['device'] or not all(
        isinstance(end['port'], str) and end['port'] and active_device(db, end['device'], end['epoch'])
        for end in (left, right)
    ):
        return {'decision': 'fabric_endpoint_unavailable'}
    if one(db, 'SELECT id FROM fabric_cables WHERE id=?', (command['cable'],)):
        return {'decision': 'fabric_cable_id_used'}
    for endpoint in (left, right):
        port = (endpoint['device'], endpoint['port'])
        if one(db, """SELECT id FROM fabric_cables WHERE connected=1 AND
            ((left_uid=? AND left_port=?) OR (right_uid=? AND right_port=?))""", port + port):
            return {'decision': 'fabric_port_occupied'}
    fields = tuple(end[field] for end in (left, right) for field in ('device', 'port', 'epoch'))
    db.execute('INSERT INTO fabric_cables VALUES(?,?,?,?,?,?,?,1)', (command['cable'], *fields))
    return {'decision': 'fabric_cable_connected'}


def exchange(db, command):
    source, destination = command['source'], command['destination']
    candidates = rows(db, """SELECT * FROM fabric_cables WHERE connected=1 AND
        ((left_uid=? AND right_uid=?) OR (left_uid=? AND right_uid=?)) ORDER BY id""",
        (source, destination, destination, source))
    for cable in candidates:
        if all(active_device(db, cable[f'{side}_uid'], cable[f'{side}_epoch']) for side in ('left', 'right')):
            db.execute('INSERT INTO fabric_exchanges VALUES(?,?,?,?,?)',
                       (command['event_id'], source, destination, cable['id'], command['payload']))
            return {'decision': 'fabric_exchange_delivered', 'cable': cable['id']}
    return {'decision': 'fabric_exchange_blocked'}


def handle(db, command):
    op = command['op']
    if op == 'plug':
        return connect_cable(db, command)
    if op == 'unplug':
        change = db.execute('UPDATE fabric_cables SET connected=0 WHERE id=? AND connected=1',
                            (command['cable'],))
        return {'decision': 'fabric_cable_disconnected' if change.rowcount else 'fabric_cable_absent'}
    if op == 'exchange':
        return exchange(db, command)
    return {'decision': 'unknown_operation'}
