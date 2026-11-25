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
    assert not [p for p in (root / "logs").iterdir() if p.name.endswith(".task.lock")]


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
    from mousedb.task_mutex import TaskMutex
    logs = root / "logs"
    logs.mkdir()
    other = TaskMutex(name=up.LOCK_MUTEX_NAME, base_dir=logs)
    assert other.try_acquire()          # a live holder: this very process
    try:
        r = _runner()
        res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    finally:
        other.release()
    assert res.skipped and not res.ok and r.calls == []
    assert "still running" in up.format_summary(res)


def test_last_update_reads_back(root):
    assert up.last_update() is None
    up.run_update(triggered_by="test", runner=_runner(), log=lambda s: None)
    assert up.last_update()["ok"] is True


# -- the update lock: judged by its holder, never by its age -----------------

def _age_everything_in(folder, hours):
    old = time.time() - hours * 3600
    for p in folder.iterdir():
        os.utime(p, (old, old))


def test_a_second_press_while_an_update_runs_is_refused(root):
    """Two presses must never both run: the second one is told who holds it."""
    second = []

    def runner(cmd):
        if not second:
            second.append(up.run_update(triggered_by="second", runner=lambda c: (0, "ok"),
                                        log=lambda s: None))
        return 0, "ok"

    first = up.run_update(triggered_by="first", runner=runner, log=lambda s: None)
    assert first.ok
    assert second[0].skipped and "still running" in second[0].message


def test_a_long_running_update_is_never_taken_over(root):
    """An update whose steps take hours is still running: its lock would otherwise be
    taken over after 2 hours by age alone, and the first update then deleted
    the second one's lock on its way out."""
    second = []

    def runner(cmd):
        if not second:
            _age_everything_in(root / "logs", hours=3)      # the first press, 3 hours in
            second.append(up.run_update(triggered_by="second", runner=lambda c: (0, "ok"),
                                        log=lambda s: None))
        return 0, "ok"

    up.run_update(triggered_by="first", runner=runner, log=lambda s: None)
    assert second[0].skipped, "a live update's lock was taken over because it was old"


def test_a_crashed_updates_lock_is_taken_over_at_once(root):
    """A lock whose holder process is gone does not block the button."""
    logs = root / "logs"
    logs.mkdir()
    from mousedb.task_mutex import TaskMutex
    TaskMutex(name=up.LOCK_MUTEX_NAME, base_dir=logs).path.write_text(
        "999999999 2026-01-01T00:00:00", encoding="utf-8")
    r = _runner()
    res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    assert not res.skipped and res.ok and r.calls == up.STEP_KEYS
    assert not [p for p in logs.iterdir() if p.name.endswith(".task.lock")]


# -- how long each step may run ----------------------------------------------

def test_the_snapshot_step_is_not_cut_off_after_an_hour(root, monkeypatch):
    """A full snapshot refresh can take more than an hour on a large database.
    The button stopped every step at one hour, so its snapshot step could
    never finish and was killed part way through rewriting the exports. The
    imports keep their limit; the snapshot step's is only a backstop against
    a hung refresh (its waits are bounded inside the refresh)."""
    import subprocess
    seen = {}

    def fake_run(cmd, **kw):
        key = [k for k, _, c in up.STEPS if c == list(cmd)][0]
        seen[key] = kw.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")

    monkeypatch.setattr(up.subprocess, "run", fake_run)
    res = up.run_update(triggered_by="test", log=lambda s: None)
    assert res.ok and set(seen) == set(up.STEP_KEYS)
    assert seen["snapshot"] is None or seen["snapshot"] > 6 * 3600
    assert set(up.STEP_TIMEOUT_SECONDS) == set(up.STEP_KEYS)
    for key in ("sheets", "reaches", "analyses", "brains"):
        assert seen[key] == up.STEP_TIMEOUT_SECONDS[key]


def test_a_step_that_overruns_its_limit_says_so(root, monkeypatch):
    import subprocess

    def fake_run(cmd, **kw):
        if kw.get("timeout"):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")

    monkeypatch.setattr(up.subprocess, "run", fake_run)
    res = up.run_update(triggered_by="test", log=lambda s: None)
    failed = {s.key: s for s in res.steps if not s.ok}
    assert set(failed) == set(up.STEP_KEYS)
    assert "stopped" in failed["sheets"].summary


def test_a_lock_freed_between_looking_and_asking_is_taken_not_refused(root, monkeypatch):
    """The other update can finish between this press's refused try and the
    look at who holds the lock; the press then runs instead of being told
    'still running (no lockfile)'."""
    from mousedb.task_mutex import TaskMutex
    real = TaskMutex._try_acquire
    tries = []

    def first_refused(self, log):
        tries.append(1)
        return False if len(tries) == 1 else real(self, log)

    monkeypatch.setattr(TaskMutex, "_try_acquire", first_refused)
    r = _runner()
    res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    assert not res.skipped and res.ok and len(tries) == 2


def test_the_update_lock_shows_it_is_alive_while_held(root, monkeypatch):
    """An update started on another computer is judged by its lockfile's age,
    so the lockfile is touched while the update runs."""
    from mousedb import task_mutex
    monkeypatch.setattr(task_mutex, "HEARTBEAT_SECONDS", 0.05)
    ages = []

    def runner(cmd):
        lock = [p for p in (root / "logs").iterdir() if p.name.endswith(".task.lock")][0]
        old = time.time() - 3600
        os.utime(lock, (old, old))
        deadline = time.time() + 5
        while lock.stat().st_mtime < time.time() - 60 and time.time() < deadline:
            time.sleep(0.05)
        ages.append(time.time() - lock.stat().st_mtime)
        return 0, "ok"

    up.run_update(triggered_by="test", skip=up.STEP_KEYS[1:], runner=runner, log=lambda s: None)
    assert ages and ages[0] < 60


# -- an update started by an earlier version of this tool --------------------

def _legacy_marker(logs, pid, started):
    logs.mkdir(exist_ok=True)
    (logs / up.LEGACY_LOCK_NAME).write_text(
        json.dumps({"pid": pid, "started": started, "triggered_by": "gui"}), encoding="utf-8")


def test_a_running_update_of_an_earlier_version_is_respected(root):
    """A window opened before an upgrade keeps the old code, which knows only
    the old marker; the two must not run at once."""
    import psutil
    from datetime import datetime as dt
    started = dt.fromtimestamp(psutil.Process().create_time() + 1).isoformat(timespec="seconds")
    _legacy_marker(root / "logs", os.getpid(), started)
    r = _runner()
    res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    assert res.skipped and r.calls == [] and "earlier version" in res.message
    assert (root / "logs" / up.LEGACY_LOCK_NAME).exists()       # not ours to remove


def _age(path, hours):
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_an_earlier_versions_update_on_another_computer_is_respected(root):
    """The earlier versions' marker names a pid but not the computer. A pid
    that does not exist here may be an update running on another computer
    that shares the logs folder, so it is not proof the update died: a marker
    younger than two hours (the earlier versions' own rule) is refused."""
    _legacy_marker(root / "logs", 999999999, "2026-01-01T00:00:00")
    _age(root / "logs" / up.LEGACY_LOCK_NAME, 5 / 60)       # started 5 minutes ago
    r = _runner()
    res = up.run_update(triggered_by="test", runner=r, log=lambda s: None)
    assert res.skipped and r.calls == [] and "earlier version" in res.message
    assert (root / "logs" / up.LEGACY_LOCK_NAME).exists()       # not ours to remove


def test_a_dead_earlier_versions_marker_is_taken_over_after_two_hours(root):
    _legacy_marker(root / "logs", 999999999, "2026-01-01T00:00:00")
    _age(root / "logs" / up.LEGACY_LOCK_NAME, 3)
    r = _runner()
    assert up.run_update(triggered_by="test", runner=r, log=lambda s: None).ok
    assert r.calls


def test_an_earlier_version_sees_this_update_running(root):
    """While this version runs, the old marker exists (so an old window
    refuses), and it is removed afterwards."""
    seen = []

    def runner(cmd):
        seen.append((root / "logs" / up.LEGACY_LOCK_NAME).exists())
        return 0, "ok"

    up.run_update(triggered_by="test", runner=runner, log=lambda s: None)
    assert seen and all(seen)
    assert not (root / "logs" / up.LEGACY_LOCK_NAME).exists()


def test_an_earlier_version_still_sees_this_update_after_two_hours(root, monkeypatch):
    """An earlier version takes over an update.lock older than two hours. This
    version's updates can run longer than that, so the marker it writes must
    stay young for as long as the update runs."""
    from mousedb import task_mutex
    monkeypatch.setattr(task_mutex, "HEARTBEAT_SECONDS", 0.05)
    refused_by_old_rule = []

    def runner(cmd):
        # the earlier versions' file name, spelled out so this test means the
        # same thing against any version
        marker = root / "logs" / "update.lock"
        old = time.time() - 3 * 3600                  # three hours into the update
        os.utime(marker, (old, old))
        deadline = time.time() + 5
        while time.time() - marker.stat().st_mtime >= 2 * 3600 and time.time() < deadline:
            time.sleep(0.05)
        # the earlier versions' own rule: younger than 2 hours means running
        refused_by_old_rule.append(time.time() - marker.stat().st_mtime < 2 * 3600)
        return 0, "ok"

    up.run_update(triggered_by="test", skip=up.STEP_KEYS[1:], runner=runner, log=lambda s: None)
    assert refused_by_old_rule == [True]


def test_two_presses_at_the_same_moment_never_both_run(root, monkeypatch):
    """The update lock would otherwise be taken by checking that a file did not exist
    and then writing it, so a second press landing between the first press's
    check and its write found no lock and ran too. Here the second press is
    made at exactly that moment: just before the first press writes any lock
    file. Exactly one of the two may run its steps."""
    import pathlib
    real_write_text = pathlib.Path.write_text
    presses = []

    def second_press_right_now(self, *a, **kw):
        if self.name.endswith("lock") and not presses:
            presses.append(None)
            r = _runner()
            presses[0] = (up.run_update(triggered_by="second", runner=r, log=lambda s: None), r)
        return real_write_text(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", second_press_right_now)
    r1 = _runner()
    first = up.run_update(triggered_by="first", runner=r1, log=lambda s: None)
    second, r2 = presses[0]
    ran = [res for res, r in ((first, r1), (second, r2)) if r.calls]
    assert len(ran) == 1, "both presses ran their steps at the same time"
    assert first.ok and second.skipped and r2.calls == []
