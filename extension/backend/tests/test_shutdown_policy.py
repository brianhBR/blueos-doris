"""Tests for the persistent AGT bench-mode override."""

import json

from doris.services import shutdown_policy


def test_deployment_default_enables_automatic_shutdown(monkeypatch, tmp_path):
    monkeypatch.setattr(
        shutdown_policy, "POLICY_PATH", tmp_path / "agt_shutdown_policy.json"
    )
    monkeypatch.setattr(shutdown_policy.settings, "agt_shutdown_enabled", True)

    assert shutdown_policy.get_shutdown_policy() == {
        "bench_mode": False,
        "automatic_payload_shutdown": True,
    }


def test_bench_mode_is_persisted_atomically(monkeypatch, tmp_path):
    path = tmp_path / "configurations" / "agt_shutdown_policy.json"
    monkeypatch.setattr(shutdown_policy, "POLICY_PATH", path)

    assert shutdown_policy.set_bench_mode(True) == {
        "bench_mode": True,
        "automatic_payload_shutdown": False,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == {"bench_mode": True}
    assert shutdown_policy.automatic_payload_shutdown_enabled() is False

    shutdown_policy.set_bench_mode(False)
    assert shutdown_policy.automatic_payload_shutdown_enabled() is True


def test_invalid_policy_falls_back_to_deployment_default(monkeypatch, tmp_path):
    path = tmp_path / "agt_shutdown_policy.json"
    path.write_text('{"bench_mode": "yes"}', encoding="utf-8")
    monkeypatch.setattr(shutdown_policy, "POLICY_PATH", path)
    monkeypatch.setattr(shutdown_policy.settings, "agt_shutdown_enabled", True)

    assert shutdown_policy.get_shutdown_policy()["bench_mode"] is False
