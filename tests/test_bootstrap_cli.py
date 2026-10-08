import json
from pathlib import Path

import pytest

from vless_control import bootstrap_cli


@pytest.fixture
def opts(tmp_path):
    return dict(config=tmp_path / "xray.json", database=tmp_path / "db.sqlite",
                binary=tmp_path / "xray", name="first", host="example.com", port=443,
                sni="www.example.com", private_key="A" * 43, short_id="a1b2")


def test_dry_run_builds_registry_config_and_tests_binary_without_writing(opts, monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap_cli.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)))
    assert bootstrap_cli.run(opts) == 0
    assert len(calls) == 1
    assert calls[0][0][1:4] == ["run", "-test", "-config"]
    assert not opts["config"].exists()


def test_apply_is_first_run_only_backed_up_atomic_and_verified(opts, monkeypatch):
    monkeypatch.setattr(bootstrap_cli.subprocess, "run", lambda *a, **k: None)
    assert bootstrap_cli.run({**opts, "apply": True}) == 0
    assert json.loads(opts["config"].read_text())["inbounds"][0]["tag"] == "first"
    assert list(opts["config"].parent.glob("*.bak")) == [opts["config"].with_suffix(".json.bak")]


def test_existing_config_refused_even_on_apply(opts, monkeypatch):
    opts["config"].write_text("preserve")
    with pytest.raises(FileExistsError):
        bootstrap_cli.run({**opts, "apply": True})
    assert opts["config"].read_text() == "preserve"


def test_failed_verification_leaves_config_unwritten(opts, monkeypatch):
    def fail(*a, **k): raise RuntimeError("invalid")
    monkeypatch.setattr(bootstrap_cli.subprocess, "run", fail)
    assert bootstrap_cli.run({**opts, "apply": True}) != 0
    assert not opts["config"].exists()
