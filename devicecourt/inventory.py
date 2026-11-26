from .store import one
SCHEMA='''
CREATE TABLE IF NOT EXISTS plugins(id TEXT PRIMARY KEY,uid TEXT,epoch INTEGER,status TEXT);
CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,plugin TEXT,epoch INTEGER,slot TEXT,serial TEXT,model TEXT,topology TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS health_samples(id TEXT PRIMARY KEY,device TEXT,epoch INTEGER,seq INTEGER,healthy INTEGER,severity TEXT,at INTEGER);
'''
def handle(db,x):
 op=x['op']
 if op=='plugin-register':
  old=one(db,'SELECT * FROM plugins WHERE id=?',(x['id'],))
  if old and x['epoch']<=old['epoch']:return {'decision':'stale_plugin'}
  db.execute('INSERT INTO plugins VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET uid=excluded.uid,epoch=excluded.epoch,status=excluded.status',(x['id'],x['uid'],x['epoch'],'active'))
  db.execute("UPDATE allocations SET status='frozen' WHERE device IN (SELECT id FROM devices WHERE plugin=? AND epoch<?) AND status IN ('reserved','running')",(x['id'],x['epoch']))
  db.execute("UPDATE devices SET status='stale' WHERE plugin=? AND epoch<?",(x['id'],x['epoch']))
  return {'decision':'plugin_active'}
 if op=='device-identify':
  p=one(db,'SELECT * FROM plugins WHERE id=?',(x['plugin'],))
  if not p or p['epoch']!=x['epoch']:return {'decision':'plugin_stale'}
  by_id=one(db,'SELECT * FROM devices WHERE id=?',(x['id'],))
  if by_id:
   if (by_id['serial'],by_id['slot'],by_id['model'],by_id['topology'])!=(x['serial'],x['slot'],x['model'],x['topology']):return {'decision':'device_identity_conflict'}
   if x['epoch']<=by_id['epoch']:return {'decision':'stale_device_report'}
   db.execute("UPDATE devices SET plugin=?,epoch=?,status='healthy' WHERE id=?",(x['plugin'],x['epoch'],x['id']))
   return {'decision':'device_reconfirmed'}
  existing=one(db,'SELECT * FROM devices WHERE slot=? AND status=?',(x['slot'],'healthy'))
  if existing:return {'decision':'reused' if existing['id']==x['id'] and existing['serial']==x['serial'] and existing['epoch']==x['epoch'] else 'slot_identity_conflict'}
  db.execute('INSERT INTO devices VALUES(?,?,?,?,?,?,?,?)',(x['id'],x['plugin'],x['epoch'],x['slot'],x['serial'],x['model'],x['topology'],'healthy'))
  return {'decision':'device_accepted'}
 if op=='health-isolate':
  d=one(db,'SELECT * FROM devices WHERE id=?',(x['device'],))
  if not d or d['epoch']!=x['epoch']:return {'decision':'stale_health'}
  last=one(db,'SELECT MAX(seq) n FROM health_samples WHERE device=? AND epoch=?',(x['device'],x['epoch']))['n']
  if last is not None and x['seq']<=last:return {'decision':'stale_health_sequence'}
  severity=x.get('severity','recoverable')
  db.execute('INSERT INTO health_samples VALUES(?,?,?,?,?,?,?)',(x['id'],x['device'],x['epoch'],x['seq'],int(x['healthy']),severity,x['at']))
  prior=one(db,'SELECT * FROM health_samples WHERE device=? AND epoch=? AND seq<? ORDER BY seq DESC LIMIT 1',(x['device'],x['epoch'],x['seq']))
  isolate=not x['healthy'] and (severity=='fatal' or (prior and not prior['healthy']))
  if isolate:
   db.execute("UPDATE devices SET status='isolated' WHERE id=?",(x['device'],))
   db.execute("UPDATE allocations SET status='frozen' WHERE device=? AND status IN ('reserved','running')",(x['device'],))
  return {'decision':'isolated' if isolate else 'degraded_pending' if not x['healthy'] else 'healthy'}
 return {'decision':'unknown_operation'}
