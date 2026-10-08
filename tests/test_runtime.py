import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from vless_control.registry import Registry
from vless_control.runtime import RuntimeService
from vless_control.deploy import XrayConfigDeployer

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root=Path(self.tmp.name)
        self.registry=Registry(root/'db.sqlite')
        self.path=root/'xray.json'
        self.original={"inbounds":[{"tag":"selected","port":9443,"protocol":"vless","streamSettings":{"security":"none","network":"tcp"},"settings":{"clients":[{"id":"manual","email":"operator"},{"id":"stale","email":"vless-control-old"}],"decryption":"none"}}],"outbounds":[{"protocol":"freedom"}]}
        self.path.write_text(json.dumps(self.original))
        self.service=RuntimeService(self.registry,host="vpn.example",port=9443,config_path=str(self.path),lock_path=str(root/'lock'),restart=lambda:None,target_tag="selected")
        patcher=patch.object(XrayConfigDeployer,"validate",lambda *a:None); patcher.start(); self.addCleanup(patcher.stop)
    def tearDown(self): self.tmp.cleanup()
    def test_create_applies_correct_port_preserving_manual_and_returns_only_after_apply(self):
        item=self.service.create("new",7)
        saved=json.loads(self.path.read_text()); clients=saved['inbounds'][0]['settings']['clients']
        self.assertEqual([c['id'] for c in clients],['manual',item['uuid']])
        self.assertEqual(item['expires_at'] is not None,True)
    def test_failure_rolls_back_and_created_record_revoked(self):
        self.service.restart=lambda: (_ for _ in ()).throw(RuntimeError('restart failed'))
        with self.assertRaises(RuntimeError): self.service.create("failed")
        self.assertEqual(self.path.read_text(),json.dumps(self.original))
        self.assertEqual(self.registry.list_users(), [])
        self.assertEqual(self.registry.list_connections(1),[])
    def test_expiry_deploy_failure_keeps_active_retryable_state(self):
        from datetime import datetime, timezone, timedelta
        item=self.service.create("expires",1)
        with self.registry._connect() as db:
            db.execute("UPDATE users SET expires_at=? WHERE id=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat().replace('+00:00','Z'), item['id']))
        self.service.restart=lambda: (_ for _ in ()).throw(RuntimeError('deploy failed'))
        observed=[]
        from vless_control.deploy import reconcile_clients
        original_reconcile=reconcile_clients
        def capture(config, assignments, **kwargs):
            observed.append(assignments)
            return original_reconcile(config, assignments, **kwargs)
        with patch('vless_control.runtime.reconcile_clients', capture):
            with self.assertRaises(RuntimeError): self.service.cleanup_expired()
        self.assertNotIn(item['uuid'], [c['id'] for c in observed[-1]['selected']])
        with self.registry._connect() as db:
            row=db.execute("SELECT status FROM users WHERE id=?",(item['id'],)).fetchone()
        self.assertEqual(row['status'],'active')
        self.service.restart=lambda:None
        self.assertEqual(self.service.cleanup_expired(),[item['id']])
        self.assertEqual(self.registry.get_user(item['id'])['status'],'revoked')

    def test_expiry_success_removes_uuid_from_target_assignment(self):
        from datetime import datetime, timezone, timedelta
        item=self.service.create("expires-success",1)
        with self.registry._connect() as db:
            db.execute("UPDATE users SET expires_at=? WHERE id=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat().replace('+00:00','Z'), item['id']))
        self.assertEqual(self.service.cleanup_expired(),[item['id']])
        clients=json.loads(self.path.read_text())['inbounds'][0]['settings']['clients']
        self.assertNotIn(item['uuid'],[c['id'] for c in clients])
        self.assertEqual(self.registry.get_user(item['id'])['status'],'revoked')

    def test_expired_user_cannot_be_resumed_or_created(self):
        from datetime import datetime, timezone, timedelta
        with self.assertRaisesRegex(ValueError, "future"):
            self.registry.add_user("already-expired", expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
        item=self.service.create("expired-resume")
        with self.registry._connect() as db:
            db.execute("UPDATE users SET expires_at=? WHERE id=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat().replace('+00:00','Z'), item['id']))
            db.execute("UPDATE users SET status='paused',active=0 WHERE id=?",(item['id'],))
        with self.assertRaisesRegex(ValueError, "expired"):
            self.service.transition(item['id'],'active')

    def test_profile_mapping_mismatch_fails_without_deploy(self):
        self.service.target_tag="wrong"
        with self.assertRaisesRegex(ValueError,"matching explicit managed tag"):
            self.service.create("mismap")
        self.assertEqual(self.registry.list_users(), [])

    def test_transition_commits_only_after_success(self):
        item=self.service.create("transition")
        self.service.restart=lambda: (_ for _ in ()).throw(RuntimeError('restart failed'))
        with self.assertRaises(RuntimeError): self.service.transition(item['id'],'paused')
        self.assertEqual(self.registry.get_user(item['id'])['status'],'active')
        self.assertEqual(json.loads(self.path.read_text()),self.original | {"inbounds":[self.original['inbounds'][0] | {"settings":self.original['inbounds'][0]['settings'] | {"clients":[{"id":"manual","email":"operator"},{"id":item['uuid'],"email":"vless-control-"+item['uuid']}]}}]})
    def test_missing_host_fail_closed(self):
        with self.assertRaises(ValueError): RuntimeService(self.registry,host="",lock_path=str(Path(self.tmp.name)/'x'))
if __name__=='__main__': unittest.main()
