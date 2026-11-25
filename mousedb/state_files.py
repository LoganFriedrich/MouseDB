"""Reading and writing the small JSON state files the importers keep beside
the data (ledgers, pending lists, manifests) -- safely.

WHY A MODULE OF ITS OWN: more than one importer keeps a ledger, and each one
had the same two failure modes. A plain ``write_text`` interrupted by the
scheduler's time limit leaves half a file; a reader that treats a file it
cannot read as EMPTY then starts again from nothing, without saying so. The
reach import was fixed first; the analysis mirror (``import_analyses``) has
the same needs, so the two helpers live here and both importers use them:

  * ``_write_json_atomically`` -- the file is always the old one or the whole
    new one, never half of either.
  * ``_read_state_file`` -- None only when the file is CONFIRMED absent (from a
    folder listing, not from a FileNotFoundError, which a dropped share also
    raises); a file that is there but cannot be read raises
    ``StateFileUnreadable`` with one line naming the file and what to do,
    instead of reading as empty.

``import_reaches`` re-exports these names, so its callers and tests are
unchanged.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional


class StateFileUnreadable(RuntimeError):
    """A state file (a ledger, the pending-cohorts list) exists but cannot be read.

    Raised instead of carrying on as if the file were empty; see
    ``_read_state_file`` for why."""


# How long a read of the ledger (or the pending-cohorts file) is retried
# before the run stops. WHY: the files live on a network share, and a share
# that drops for a moment answers with an OSError that is gone a few seconds
# later. That should cost a few seconds, not a failed night.
STATE_READ_ATTEMPTS = 5
STATE_READ_RETRY_SECONDS = 2.0


def _confirmed_absent(p: Path) -> bool:
    """True only when a folder above ``p`` can be listed and the listing shows
    that ``p`` is not there.

    WHY: a FileNotFoundError alone does not mean the file is missing. On
    Windows a mapped network drive that is briefly unreachable answers
    "network path not found" / "network name not found", and Python raises
    those as FileNotFoundError too. Read as "no ledger", that one moment
    re-imported every results file (hours, under the database lock), and read
    as "no cohorts waiting", the next save of that list dropped every cohort
    on it. So "missing" is believed only from a folder that answers: the
    nearest folder above ``p`` that can be listed must not name the next part
    of the path (a new database has no logs folder yet, which is the same
    answer one level up). A folder that cannot be listed at all, up to the
    root of the drive, means the share is not answering -- not a missing
    file."""
    child = p
    folder = p.parent
    while True:
        try:
            with os.scandir(folder) as it:
                names = {os.path.normcase(e.name) for e in it}
        except FileNotFoundError:
            if folder.parent == folder:
                return False            # even the drive root is not there
            child, folder = folder, folder.parent
            continue
        except OSError:
            return False
        return os.path.normcase(child.name) not in names


def _read_state_file(p: Path, what: str, kind: Any, start_over: str,
                     consequence: str) -> Optional[Any]:
    """The parsed content of ``p``; None when the file does not exist.

    WHY THIS REFUSES INSTEAD OF STARTING AFRESH: the ledger is the only record
    of which results files are already in the database. A ledger that could not be read (a share error, a file another
    program had damaged) must not be treated as EMPTY: the run would import every
    file again, every reach of every filed video rewritten over the share, a run of
    several hours that holds the database lock all that time -- and nothing
    would say why. A ledger that is truly missing (a new database, a new machine)
    still starts empty: then importing everything is the right thing to do --
    but "truly missing" is believed only from a folder listing
    (``_confirmed_absent``), never from a FileNotFoundError alone, which a
    dropped network share also raises.
    A ledger that is there but unreadable stops the run with one line naming
    the file, what the stop means (``consequence``: whether anything was
    imported before it) and what to do (``start_over``).

    A read that fails with an OSError is retried for a few seconds first
    (``STATE_READ_ATTEMPTS``); content that is not JSON, or not a ``kind``,
    is refused at once -- reading it again would not change it."""
    last: Optional[BaseException] = None
    for attempt in range(STATE_READ_ATTEMPTS):
        try:
            text = p.read_text(encoding="utf-8")
            break
        except FileNotFoundError as e:
            if _confirmed_absent(p):
                return None
            last = e                    # the share, not the file: retry, then refuse
            if attempt + 1 < STATE_READ_ATTEMPTS:
                time.sleep(STATE_READ_RETRY_SECONDS)
        except OSError as e:
            last = e
            if attempt + 1 < STATE_READ_ATTEMPTS:
                time.sleep(STATE_READ_RETRY_SECONDS)
    else:
        raise StateFileUnreadable(
            "the %s %s cannot be read (%s: %s); %s. If the share "
            "was briefly unavailable, run again; %s"
            % (what, p, type(last).__name__, last, consequence, start_over))
    try:
        data = json.loads(text)
    except ValueError as e:
        raise StateFileUnreadable(
            "the %s %s is not valid JSON (%s); %s. %s"
            % (what, p, e, consequence, start_over[:1].upper() + start_over[1:]))
    if not isinstance(data, kind):
        wanted = " or ".join(k.__name__ for k in (kind if isinstance(kind, tuple) else (kind,)))
        raise StateFileUnreadable(
            "the %s %s holds a %s, not a %s; %s. %s"
            % (what, p, type(data).__name__, wanted, consequence,
               start_over[:1].upper() + start_over[1:]))
    return data


def _write_json_atomically(p: Path, obj: Any) -> None:
    """Write ``obj`` to ``p`` so that ``p`` is always either the old file or the
    whole new one, never half of either.

    WHY: the scheduler ends a run that reaches its time limit wherever it is,
    and a state file can be mid-write at that moment (the reach import saves
    its ledger during the run, see ``import_reaches.LEDGER_SAVE_SECONDS``; the
    analysis mirror saves its ledger and manifest once, at the end). A kill in
    the middle of a plain write_text left a truncated file, which the next
    run cannot read -- the reach import would otherwise start a NEW ledger and
    re-import every file, and the Where Is My Data tab showed a half-written
    manifest as unreadable. The new content goes to a temporary file beside
    the target first and is swapped in whole with os.replace. On Windows the
    swap fails while another program has the file open (the census and the
    status tab read these files for a moment), so it is retried briefly."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(".%s.%d.tmp" % (p.name, os.getpid()))
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, indent=1, sort_keys=True))
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(20):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)
