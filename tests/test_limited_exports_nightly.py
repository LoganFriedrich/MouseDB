"""The nightly snapshot job remakes saved limited exports -- after a complete export only.

WHY: a limited export that is not remade goes stale the first night anything it
holds changes, while still looking current. It is remade right after the full
export it is cut from, never after an incomplete one (a slice of a partial
export would be partial too), and one that cannot be made fails the job so it
is seen.
"""
from mousedb.exporters import subsets


def test_the_nightly_job_remakes_saved_slices_after_a_complete_export(monkeypatch):
    from mousedb.exporters import current, refresh_snapshot
    calls = []
    monkeypatch.setattr(refresh_snapshot, "watcher_blocks_db", lambda: False)
    monkeypatch.setattr(current, "refresh_current",
                        lambda **kw: {"files": {}, "complete": True, "problems": []})
    monkeypatch.setattr(subsets, "make_saved", lambda **kw: calls.append(kw) or 0)
    assert refresh_snapshot._finish({"x": 1}, force=False) == 0
    assert calls and calls[0]["if_changed"] is True
    monkeypatch.setattr(subsets, "make_saved", lambda **kw: 1)
    assert refresh_snapshot._finish({"x": 1}, force=False) == 1     # a failed slice fails the job
    calls.clear()
    monkeypatch.setattr(subsets, "make_saved", lambda **kw: calls.append(kw) or 0)
    monkeypatch.setattr(current, "refresh_current",
                        lambda **kw: {"files": {}, "complete": False, "problems": ["x"]})
    assert refresh_snapshot._finish({"x": 1}, force=False) == 1
    assert not calls                                                  # never cut from a bad export
