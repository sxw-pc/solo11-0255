from . import packet
from .store import one
SCHEMA='''
CREATE TABLE IF NOT EXISTS start_receipts(id TEXT PRIMARY KEY,allocation TEXT,device TEXT,plugin_epoch INTEGER,container_uid TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS detach_receipts(id TEXT PRIMARY KEY,allocation TEXT,container_uid TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS recovery_cases(id TEXT PRIMARY KEY,allocation TEXT,reason TEXT,status TEXT);
'''
def handle(db,x):
 op=x['op']
 if op=='packet-execute':
  return packet.execute(db,x)
 if op=='startup-confirm':
  a=one(db,'SELECT * FROM allocations WHERE id=?',(x['allocation'],))
  d=one(db,'SELECT * FROM devices WHERE id=?',(a['device'],)) if a else None
  p=one(db,'SELECT * FROM plugins WHERE id=?',(d['plugin'],)) if d else None
  if not a or a['status']!='reserved' or not d or d['status']!='healthy' or not p or a['device']!=x['device'] or a['device_epoch']!=x['plugin_epoch'] or p['epoch']!=x['plugin_epoch'] or a['holder']!=x['container_uid']:
   return {'decision':'startup_stale'}
  db.execute('INSERT INTO start_receipts VALUES(?,?,?,?,?,?)',(x['id'],a['id'],d['id'],p['epoch'],x['container_uid'],'accepted'))
  db.execute("UPDATE allocations SET status='running' WHERE id=?",(a['id'],))
  return {'decision':'device_delivered'}
 if op=='failure-evacuate':
  a=one(db,'SELECT * FROM allocations WHERE id=?',(x['allocation'],))
  if not a or a['status'] not in ('running','frozen'):return {'decision':'evacuation_unavailable'}
  db.execute('INSERT INTO recovery_cases VALUES(?,?,?,?)',(x['id'],a['id'],x['reason'],'pending'))
  db.execute("UPDATE allocations SET status='frozen' WHERE id=?",(a['id'],))
  return {'decision':'evacuation_pending'}
 if op=='rebind-confirm':
  a=one(db,'SELECT * FROM allocations WHERE id=?',(x['allocation'],))
  d=one(db,'SELECT * FROM devices WHERE id=?',(a['device'],)) if a else None
  p=one(db,'SELECT * FROM plugins WHERE id=?',(d['plugin'],)) if d else None
  if not a or a['status']!='frozen' or a['holder']!=x['container_uid'] or not d or d['status']!='healthy' or not p or p['epoch']!=d['epoch'] or x['plugin_epoch']!=d['epoch'] or not x.get('runtime_receipt'):
   return {'decision':'rebind_unproven'}
  db.execute('UPDATE allocations SET status=?,device_epoch=?,generation=generation+1 WHERE id=?',('running',d['epoch'],a['id']))
  db.execute('INSERT INTO start_receipts VALUES(?,?,?,?,?,?)',(x['id'],a['id'],d['id'],d['epoch'],x['container_uid'],'rebound'))
  return {'decision':'device_rebound'}
 if op=='detach':
  a=one(db,'SELECT * FROM allocations WHERE id=?',(x['allocation'],))
  if not a or a['status'] not in ('running','frozen') or a['holder']!=x['container_uid']:return {'decision':'detach_denied'}
  db.execute('INSERT INTO detach_receipts VALUES(?,?,?,?)',(x['id'],a['id'],x['container_uid'],'detached'))
  db.execute("UPDATE recovery_cases SET status='detached' WHERE allocation=? AND status='pending'",(a['id'],))
  return {'decision':'detached'}
 return {'decision':'unknown_operation'}
