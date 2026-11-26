import json,tempfile,unittest
from pathlib import Path
import devicecourt
from devicecourt.store import connect,run,replay,one
ROOT=Path(__file__).resolve().parents[1]
class Business(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite';self.db=connect(str(self.path))
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def steps(self):return json.loads((ROOT/'fixtures/path.json').read_text(encoding='utf-8'))
 def test_delivery_failure_and_quota_recovery(self):
  out=[run(self.db,x['command'],x['input']) for x in self.steps()]
  self.assertEqual(out[5]['decision'],'device_delivered');self.assertEqual(out[6]['decision'],'isolated')
  self.assertEqual(out[8]['decision'],'release_unproven');self.assertEqual(out[-1]['decision'],'quota_returned')
  self.assertTrue(replay(str(self.path))['verified'])
 def test_second_tenant_cannot_share_reserved_device(self):
  for x in self.steps()[:5]:run(self.db,x['command'],x['input'])
  run(self.db,'allocate',{'event_id':'q2','op':'quota','tenant':'t2','limit':1})
  run(self.db,'allocate',{'event_id':'req2','op':'request-evaluate','id':'req-2','tenant':'t2','model':'gpu-x','topology':'rack-a'})
  self.assertEqual(run(self.db,'allocate',{'event_id':'reserve2','op':'device-reserve','id':'alloc-2','request':'req-2','device':'gpu-7','holder':'container-2'})['decision'],'reserve_denied')
 def test_plugin_epoch_invalidates_old_startup(self):
  for x in self.steps()[:5]:run(self.db,x['command'],x['input'])
  run(self.db,'inventory',{'event_id':'restart','op':'plugin-register','id':'plugin-a','uid':'plugin-new','epoch':2})
  self.assertEqual(run(self.db,'runtime',self.steps()[5]['input'])['decision'],'startup_stale')
 def test_same_slot_new_serial_needs_new_plugin_epoch(self):
  for x in self.steps()[:2]:run(self.db,x['command'],x['input'])
  forged=dict(self.steps()[1]['input'],event_id='replace',id='gpu-8',serial='serial-8')
  self.assertEqual(run(self.db,'inventory',forged)['decision'],'slot_identity_conflict')
 def test_late_health_sample_cannot_reopen_isolated_device(self):
  for x in self.steps()[:7]:run(self.db,x['command'],x['input'])
  old=dict(self.steps()[6]['input'],event_id='late',id='old-health',seq=1,healthy=True)
  self.assertEqual(run(self.db,'inventory',old)['decision'],'stale_health_sequence')
  self.assertEqual(one(self.db,'SELECT status FROM devices WHERE id=?',('gpu-7',))['status'],'isolated')
 def test_topology_mismatch_changes_request_result(self):
  for x in self.steps()[:3]:run(self.db,x['command'],x['input'])
  request=dict(self.steps()[3]['input'],event_id='other-topology',id='req-wrong',topology='rack-b')
  self.assertEqual(run(self.db,'allocate',request)['decision'],'no_eligible_device')
 def test_wrong_container_detach_does_not_return_quota(self):
  for x in self.steps()[:8]:run(self.db,x['command'],x['input'])
  forged=dict(self.steps()[9]['input'],event_id='wrong-detach',id='wrong-detach',container_uid='other')
  self.assertEqual(run(self.db,'runtime',forged)['decision'],'detach_denied')
  self.assertEqual(run(self.db,'allocate',{'event_id':'return','op':'quota-return','allocation':'alloc-1'})['decision'],'release_unproven')
  self.assertEqual(one(self.db,'SELECT used FROM quota_ledgers WHERE tenant=?',('t1',))['used'],1)
 def test_recoverable_noise_needs_repeated_failure_before_isolation(self):
  p=self.steps()
  for x in p[:6]:run(self.db,x['command'],x['input'])
  noise=dict(p[6]['input'],event_id='noise',id='noise',severity='recoverable')
  self.assertEqual(run(self.db,'inventory',noise)['decision'],'degraded_pending')
  self.assertEqual(one(self.db,'SELECT status FROM allocations WHERE id=?',('alloc-1',))['status'],'running')
  repeated=dict(p[6]['input'],event_id='repeat',id='repeat',seq=2,severity='recoverable')
  self.assertEqual(run(self.db,'inventory',repeated)['decision'],'isolated')
  self.assertEqual(one(self.db,'SELECT status FROM allocations WHERE id=?',('alloc-1',))['status'],'frozen')
 def test_request_cannot_reserve_two_physical_devices(self):
  p=self.steps()
  for x in p[:5]:run(self.db,x['command'],x['input'])
  run(self.db,'inventory',{'event_id':'gpu-8','op':'device-identify','id':'gpu-8','plugin':'plugin-a','epoch':1,'slot':'0000:02','serial':'serial-8','model':'gpu-x','topology':'rack-a'})
  run(self.db,'allocate',{'event_id':'quota-2','op':'quota','tenant':'t1','limit':2})
  self.assertEqual(run(self.db,'allocate',{'event_id':'double','op':'device-reserve','id':'alloc-2','request':'req-1','device':'gpu-8','holder':'container-1'})['decision'],'reserve_denied')
 def test_plugin_reconfirmation_requires_same_physical_identity(self):
  p=self.steps()
  for x in p[:2]:run(self.db,x['command'],x['input'])
  run(self.db,'inventory',{'event_id':'plugin-new','op':'plugin-register','id':'plugin-a','uid':'plugin-new','epoch':2})
  again=dict(p[1]['input'],event_id='confirm',epoch=2)
  self.assertEqual(run(self.db,'inventory',again)['decision'],'device_reconfirmed')
  forged=dict(again,event_id='forged',serial='other')
  self.assertEqual(run(self.db,'inventory',forged)['decision'],'device_identity_conflict')
 def test_plugin_restart_freezes_running_allocation_until_runtime_rebind(self):
  p=self.steps()
  for x in p[:6]:run(self.db,x['command'],x['input'])
  run(self.db,'inventory',{'event_id':'plugin-new','op':'plugin-register','id':'plugin-a','uid':'plugin-new','epoch':2})
  self.assertEqual(one(self.db,'SELECT status FROM allocations WHERE id=?',('alloc-1',))['status'],'frozen')
  report=dict(p[1]['input'],event_id='reconfirm',epoch=2)
  run(self.db,'inventory',report)
  old={'event_id':'old-rebind','op':'rebind-confirm','id':'old-rebind','allocation':'alloc-1','container_uid':'container-1','plugin_epoch':1,'runtime_receipt':'runtime-old'}
  self.assertEqual(run(self.db,'runtime',old)['decision'],'rebind_unproven')
  new=dict(old,event_id='new-rebind',id='new-rebind',plugin_epoch=2,runtime_receipt='runtime-new')
  self.assertEqual(run(self.db,'runtime',new)['decision'],'device_rebound')
  self.assertEqual(one(self.db,'SELECT status FROM allocations WHERE id=?',('alloc-1',))['status'],'running')
 def test_each_fixture_runs_from_empty_database_and_replays(self):
  expected={'inventory': 'device_accepted', 'allocate': 'reserved', 'runtime': 'device_delivered'}
  for c in ('inventory', 'allocate', 'runtime'):
   for suffix in ('normal','failure'):
    with self.subTest(command=c,variant=suffix), tempfile.TemporaryDirectory() as td:
     path=Path(td)/'fixture.sqlite';db=connect(str(path))
     commands=json.loads((ROOT/'fixtures'/f'{c}_{suffix}.json').read_text(encoding='utf-8'))
     results=[run(db,x['command'],x['input']) for x in commands]
     db.close()
     self.assertEqual(results[-1]['decision'],expected[c] if suffix=='normal' else {'inventory': 'stale_health', 'allocate': 'reserve_denied', 'runtime': 'startup_stale'}[c])
     self.assertTrue(replay(str(path))['verified'])
  self.assertEqual(Path(devicecourt.__file__).resolve().parents[1],ROOT)
print('module_file',devicecourt.__file__)
