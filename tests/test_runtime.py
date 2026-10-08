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
        with self.registry._connect() as db:
            profile=db.execute("SELECT * FROM profiles").fetchone()
        self.assertEqual((profile['name'],profile['host'],profile['port']),('selected','vpn.example',9443))
    def test_existing_managed_profile_mismatch_fails_closed(self):
        self.registry.add_profile('selected','wrong.example',1,'none','tcp')
        with self.assertRaisesRegex(ValueError,'does not exactly match'):
            self.service.create('must-not-create')
        self.assertEqual(self.registry.list_users(), [])
    def test_managed_port_must_match_environment(self):
        with patch.dict('os.environ', {'XRAY_MANAGED_VLESS_PORT':'443'}):
            with self.assertRaisesRegex(ValueError,'does not match'):
                RuntimeService(self.registry,host='vpn.example',port=1,target_tag='selected')
    def test_profile_uses_configured_443_host_and_tag_for_one_day_key(self):
        self.path.write_text(json.dumps({"inbounds":[{"tag":"managed","port":443,"protocol":"vless","streamSettings":{"security":"none","network":"tcp"},"settings":{"clients":[],"decryption":"none"}}],"outbounds":[{"protocol":"freedom"}]}))
        self.service=RuntimeService(self.registry,host='vpn.example',port=443,config_path=str(self.path),lock_path=str(Path(self.tmp.name)/'lock443'),restart=lambda:None,target_tag='managed')
        item=self.service.create('duration-check',1)
        self.assertTrue(item['expires_at'])
        with self.registry._connect() as db:
            profile=db.execute('SELECT name,host,port FROM profiles').fetchone()
        self.assertEqual(tuple(profile),('managed','vpn.example',443))
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
    def test_delete_user_removes_all_managed_inbound_clients(self):
        item = self.service.create("delete-all")
        ws = {"tag":"vless-control-ws-8088","port":10001,"protocol":"vless","settings":{"clients":[],"decryption":"none"},"streamSettings":{"network":"ws","security":"none","wsSettings":{"path":"/vless-ws"}}}
        c=json.loads(self.path.read_text()); c["inbounds"].append(ws); self.path.write_text(json.dumps(c))
        ws_id=self.registry.add_profile("vless-control-ws-8088","vpn.example",8088,"none","ws",path="/vless-ws")
        self.registry.assign_profiles(item["id"],[ws_id])
        self.service.delete(item["id"])
        saved=json.loads(self.path.read_text())
        self.assertTrue(all(item["uuid"] not in [x.get("id") for x in i.get("settings",{}).get("clients",[])] for i in saved["inbounds"]))

    def test_delete_key_removes_only_selected_profile_and_preserves_other(self):
        item=self.service.create("delete-one-profile")
        ws={"tag":"vless-control-ws-8088","port":10001,"protocol":"vless","settings":{"clients":[],"decryption":"none"},"streamSettings":{"network":"ws","security":"none","wsSettings":{"path":"/vless-ws"}}}
        config=json.loads(self.path.read_text()); config["inbounds"].append(ws); self.path.write_text(json.dumps(config))
        ws_id=self.registry.add_profile("vless-control-ws-8088","vpn.example",8088,"none","ws",path="/vless-ws")
        self.registry.assign_profiles(item["id"],[ws_id])
        tcp_id=self.registry.list_connections(item["id"])[0]["id"]
        self.assertTrue(self.service.delete_key(item["id"],ws_id))
        saved=json.loads(self.path.read_text())
        self.assertNotIn(item["uuid"],[x.get("id") for x in saved["inbounds"][1]["settings"]["clients"]])
        self.assertIn(item["uuid"],[x.get("id") for x in saved["inbounds"][0]["settings"]["clients"]])

    def test_delete_key_rolls_back_mapping_when_deploy_fails(self):
        item=self.service.create("delete-one-fail")
        ws={"tag":"vless-control-ws-8088","port":10001,"protocol":"vless","settings":{"clients":[],"decryption":"none"},"streamSettings":{"network":"ws","security":"none","wsSettings":{"path":"/vless-ws"}}}
        config=json.loads(self.path.read_text()); config["inbounds"].append(ws); self.path.write_text(json.dumps(config))
        ws_id=self.registry.add_profile("vless-control-ws-8088","vpn.example",8088,"none","ws",path="/vless-ws")
        self.registry.assign_profiles(item["id"],[ws_id])
        self.service.restart=lambda: (_ for _ in ()).throw(RuntimeError("restart failed"))
        with self.assertRaises(RuntimeError): self.service.delete_key(item["id"],ws_id)
        self.assertEqual(len(self.registry.list_connections(item["id"])),2)

    def test_monitor_api_failure_is_not_reported_as_offline(self):
        item=self.service.create("monitor-api-down")
        with patch("vless_control.runtime.subprocess.run", side_effect=FileNotFoundError()):
            report=self.service.monitor()
        self.assertIsNone(report[0]["online"])
        self.assertIn("monitor_error",report[0])

    def test_monitor_refuses_non_loopback_api_inbound(self):
        item=self.service.create("monitor-api-public")
        config=json.loads(self.path.read_text())
        config["api"]={"tag":"api","services":["StatsService","HandlerService"]}
        config["stats"]={}
        config["inbounds"].append({"tag":"api","listen":"0.0.0.0","port":10085,"protocol":"dokodemo-door"})
        config["routing"]={"rules":[{"type":"field","inboundTag":["api"],"outboundTag":"api"}]}
        self.path.write_text(json.dumps(config))
        report=self.service.monitor()
        self.assertIsNone(report[0]["online"])
        self.assertIn("monitor_error",report[0])

    def test_missing_host_fail_closed(self):
        with self.assertRaises(ValueError): RuntimeService(self.registry,host="",lock_path=str(Path(self.tmp.name)/'x'))
if __name__=='__main__': unittest.main()
