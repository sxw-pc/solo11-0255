from . import slices
from .store import one,rows
SCHEMA='''
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,tenant TEXT,model TEXT,topology TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS allocations(id TEXT PRIMARY KEY,request TEXT,device TEXT,device_epoch INTEGER,holder TEXT,generation INTEGER,status TEXT);
CREATE TABLE IF NOT EXISTS quota_ledgers(tenant TEXT PRIMARY KEY,limit_count INTEGER,used INTEGER);
''' + slices.ALLOCATION_SCHEMA
def handle(db,x):
 op=x['op']
 if op=='slice-reserve':
  return slices.reserve(db,x)
 if op=='slice-prepare':
  return slices.prepare(db,x)
 if op=='slice-commit':
  return slices.commit(db,x)
 if op=='slice-reclaim':
  return slices.reclaim(db,x)
 if op=='slice-abort':
  return slices.abort(db,x)
 if op=='slice-water':
  return slices.water(db,x)
 if op=='slice-pause':
  return slices.pause(db,x)
 if op=='quota':
  if x['limit']<1:return {'decision':'invalid_quota'}
  db.execute('INSERT INTO quota_ledgers VALUES(?,?,0) ON CONFLICT(tenant) DO UPDATE SET limit_count=excluded.limit_count',(x['tenant'],x['limit']))
  return {'decision':'quota_active'}
 if op=='request-evaluate':
  candidates=rows(db,"SELECT d.* FROM devices d JOIN plugins p ON p.id=d.plugin WHERE d.model=? AND d.topology=? AND d.status='healthy' AND d.epoch=p.epoch AND p.status='active'",(x['model'],x['topology']))
  if not candidates:return {'decision':'no_eligible_device'}
  db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(x['id'],x['tenant'],x['model'],x['topology'],'eligible'))
  return {'decision':'eligible','devices':[d['id'] for d in candidates]}
 if op=='device-reserve':
  req=one(db,'SELECT * FROM requests WHERE id=?',(x['request'],))
  dev=one(db,'SELECT * FROM devices WHERE id=?',(x['device'],))
  q=one(db,'SELECT * FROM quota_ledgers WHERE tenant=?',(req['tenant'],)) if req else None
  occupied=one(db,"SELECT 1 FROM allocations WHERE device=? AND status IN ('reserved','running','frozen')",(x['device'],))
  already=one(db,"SELECT 1 FROM allocations WHERE request=? AND status IN ('reserved','running','frozen')",(x['request'],))
  if not req or not dev or req['status']!='eligible' or dev['status']!='healthy' or req['model']!=dev['model'] or req['topology']!=dev['topology'] or not q or q['used']>=q['limit_count'] or occupied or already:
   return {'decision':'reserve_denied'}
  db.execute('INSERT INTO allocations VALUES(?,?,?,?,?,?,?)',(x['id'],x['request'],x['device'],dev['epoch'],x['holder'],1,'reserved'))
  db.execute('UPDATE quota_ledgers SET used=used+1 WHERE tenant=?',(req['tenant'],))
  return {'decision':'reserved','device_epoch':dev['epoch']}
 if op=='quota-return':
  a=one(db,'SELECT * FROM allocations WHERE id=?',(x['allocation'],))
  receipt=one(db,'SELECT * FROM detach_receipts WHERE allocation=? AND status=?',(x['allocation'],'detached'))
  if not a or a['status'] not in ('running','frozen') or not receipt:return {'decision':'release_unproven'}
  req=one(db,'SELECT * FROM requests WHERE id=?',(a['request'],))
  db.execute("UPDATE allocations SET status='released' WHERE id=?",(a['id'],))
  db.execute('UPDATE quota_ledgers SET used=used-1 WHERE tenant=?',(req['tenant'],))
  return {'decision':'quota_returned'}
 return {'decision':'unknown_operation'}
