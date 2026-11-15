"""Every package the core code needs at run time is declared in pyproject.toml.

WHY: the database-task lock (mousedb.task_mutex) judges whether a lock's holder
is still running with psutil, and so do the watcher check before a snapshot and
the bench scan. Without psutil a lock whose holder died cannot be told from a
live one, and a wrapper that ends a stopped job's processes has nothing to
watch them with. psutil happened to be installed, but nothing declared it, so a
freshly built environment could lack it and nothing would say so.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _core_dependencies():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.S | re.M)
    assert block, "pyproject.toml has no [project] dependencies list"
    names = set()
    for item in re.findall(r'"([^"]+)"', block.group(1)):
        names.add(re.split(r"[<>=!~\[; ]", item, 1)[0].strip().lower())
    return names


def _core_modules_importing(module):
    pattern = re.compile(r"^\s*(import|from)\s+%s\b" % re.escape(module), re.M)
    return sorted(str(p.relative_to(ROOT)) for p in (ROOT / "mousedb").rglob("*.py")
                  if "endpoint_ck_analysis" not in p.parts
                  and pattern.search(p.read_text(encoding="utf-8", errors="replace")))


def test_psutil_is_a_declared_dependency():
    users = _core_modules_importing("psutil")
    assert users, "nothing imports psutil any more; drop this test and the dependency"
    assert "psutil" in _core_dependencies(), (
        "psutil is imported by %s but not declared in pyproject.toml" % ", ".join(users))
