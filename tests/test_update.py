"""mousedb update: every pull in order, under one lock, with a spelled-out result."""
import json
import os
import time

import pytest

import mousedb.update as up


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "require", lambda key: tmp_path)
    return tmp_path


def _runner(fail=()):
    calls = []

    def run(cmd):
        key = [k for k, _, c in up.STEPS if c == list(cmd)][0]
        calls.append(key)
        if key in fail:
            return 1, "working...\nsomething went wrong in %s" % key
        return 0, "working...\n%s: 3 things imported" % key
    run.calls = calls
    return run


def test_runs_every_step_in_order_and_records(root):
    r = _runner()
    res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    assert r.calls == up.STEP_KEYS
    assert res.ok and res.message == "Everything landed."
    assert [s.summary for s in res.steps] == ["%s: 3 things imported" % k for k in up.STEP_KEYS]
    last = json.loads((root / "logs" / up.LAST_NAME).read_text(encoding="utf-8"))
    assert last["ok"] is True and last["triggered_by"] == "test"
    ledger = (root / "logs" / up.LEDGER_NAME).read_text(encoding="utf-8").splitlines()
    assert len(ledger) == 1
    assert not (root / "logs" / up.LOCK_NAME).exists()


def test_a_failed_step_does_not_stop_the_rest_and_fails_loudly(root):
    r = _runner(fail={"sheets"})
    res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    assert r.calls == up.STEP_KEYS          # reaches etc. still ran after sheets failed
    assert not res.ok
    assert "1 step(s) failed" in res.message and "Tracking sheets" in res.message
    text = up.format_summary(res)
    assert "[FAIL]" in text and "something went wrong in sheets" in text and "[OK]" in text


def test_skip_leaves_a_step_out(root):
    r = _runner()
    res = up.run_update(triggered_by="test", skip=("sheets",), runner=r, log=lambda s: None)
    assert r.calls == [k for k in up.STEP_KEYS if k != "sheets"]
    assert res.ok


def test_a_running_update_holds_the_lock(root):
    logs = root / "logs"
    logs.mkdir()
    (logs / up.LOCK_NAME).write_text(json.dumps({"started": "now", "triggered_by": "gui"}), encoding="utf-8")
    r = _runner()
    res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    assert res.skipped and not res.ok and r.calls == []
    assert "still running" in up.format_summary(res)


def test_a_stale_lock_is_taken_over(root):
    logs = root / "logs"
    logs.mkdir()
    lock = logs / up.LOCK_NAME
    lock.write_text(json.dumps({"started": "long ago", "triggered_by": "gui"}), encoding="utf-8")
    old = time.time() - up.STALE_LOCK_SECONDS - 60
    os.utime(lock, (old, old))
    r = _runner()
    res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    assert not res.skipped and res.ok and r.calls == up.STEP_KEYS


def test_last_update_reads_back(root):
    assert up.last_update() is None
    up.run_update(triggered_by="test", runner=_runner(), log=lambda s: None)
    assert up.last_update()["ok"] is True
