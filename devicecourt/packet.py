"""Deliver fixed local byte packets to two synthetic device decoders."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS packet_profiles(
    device TEXT NOT NULL, epoch INTEGER NOT NULL, family TEXT NOT NULL,
    PRIMARY KEY(device,epoch)
);
CREATE TABLE IF NOT EXISTS packet_batch_modes(
    id TEXT PRIMARY KEY, mode INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS packet_runs(
    event_id TEXT PRIMARY KEY, allocation TEXT NOT NULL, family TEXT NOT NULL,
    packet TEXT NOT NULL, width INTEGER NOT NULL, stride INTEGER NOT NULL,
    work INTEGER NOT NULL
);
"""


def bind_profile(db, cmd):
    device = one(db, """SELECT d.id FROM devices d JOIN plugins p ON p.id=d.plugin
        WHERE d.id=? AND d.status='healthy' AND d.epoch=? AND d.epoch=p.epoch""",
        (cmd['device'],cmd['epoch']))
    if not device or cmd['family'] not in ('E','W'):
        return {'decision':'packet_profile_unavailable'}
    repo = (device['id'],cmd['epoch'])
    old = one(db,'SELECT family FROM packet_profiles WHERE device=? AND epoch=?',repo)
    if old:
        outcome = 'packet_profile_reused' if old['family'] == cmd['family'] else 'packet_profile_conflict'
        return {'decision':outcome}
    db.execute('INSERT INTO packet_profiles VALUES(?,?,?)',(*repo,cmd['family']))
    return {'decision':'packet_profile_recorded'}


def latch_mode(db, cmd):
    if type(cmd['mode']) is not int or cmd['mode'] not in (0,1):
        return {'decision': 'invalid_batch_mode'}
    previous = one(db, 'SELECT mode FROM packet_batch_modes WHERE id=?', (cmd['batch'],))
    if previous:
        outcome = 'packet_batch_reused' if previous['mode'] == cmd['mode'] else 'packet_mode_latched'
        return {'decision': outcome}
    db.execute('INSERT INTO packet_batch_modes VALUES(?,?)', (cmd['batch'],cmd['mode']))
    return {'decision':'packet_mode_latched_new','mode':cmd['mode']}


def execute(db, cmd):
    lease = one(db,"""SELECT a.id, q.family FROM allocations a JOIN devices d ON d.id=a.device
        JOIN plugins p ON p.id=d.plugin JOIN packet_profiles q ON q.device=d.id AND q.epoch=d.epoch
        WHERE a.id=? AND a.status='running' AND a.holder=? AND a.generation=?
          AND d.status='healthy' AND d.epoch=a.device_epoch AND p.epoch=d.epoch""",
        (cmd['allocation'],cmd['container'],cmd['generation']))
    if not lease:
        return {'decision':'packet_target_unavailable'}
    fields = cmd['packet']
    if not isinstance(fields, list) or len(fields) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in fields):
        return {'decision':'invalid_command_bytes'}
    mode,x,y = fields
    if 'batch' in cmd:
        batch = one(db, 'SELECT mode FROM packet_batch_modes WHERE id=?', (cmd['batch'],))
        if not batch or batch['mode'] != mode:
            return {'decision':'packet_mode_latched'}
    if mode == 0:
        width,stride = (x,y) if lease['family'] == 'E' else (y,x)
    elif mode == 1 and x <= 127:
        width,stride = 2*x,y
    else:
        return {'decision':'unsupported_command_format'}
    if not 1 <= width <= 255 or not 1 <= stride <= 255:
        return {'decision':'invalid_command_operands'}
    work = width * stride
    db.execute('INSERT INTO packet_runs VALUES(?,?,?,?,?,?,?)',
               (cmd['event_id'], lease['id'], lease['family'], bytes(fields).hex(), width, stride, work))
    return {'decision':'packet_executed', 'width':width, 'stride':stride, 'work':work}
