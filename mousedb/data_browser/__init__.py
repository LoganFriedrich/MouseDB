"""The data browser: point-and-click, read-only browsing of what mousedb holds.

WHAT IT IS
----------
A web page, opened in the person's own browser, that shows every table the
analysis snapshot holds plus a few ready-made tables for the common questions
(one row per animal, per animal per testing day, per reach, per brain region).
Click a column to sort, click a value to filter, press "CSV" to save what is on
screen. No SQL. The page is served by Datasette (https://datasette.io), run
inside this process, bound to this computer only (127.0.0.1).

Operator guide: docs/DATA_BROWSER.md. Why Datasette and not a desktop SQLite
browser: the same document, "Why this tool".

WHY IT WORKS ON A PRIVATE COPY -- THE LOCK PROBLEM
---------------------------------------------------
Two files must never be held open by a viewer:

* The LIVE database. It sits on a network share in rollback-journal mode, so
  any reader holds a lock that makes a committing writer wait; a viewer left
  open is a reader that never goes away. A lock there is what made the nightly
  imports fail with "database is locked" for days (2026-10). The browser never
  opens it: it does not even know its path except to refuse it.
* The SNAPSHOT files. The nightly snapshot job replaces each
  ``<snapshot_dir>/<table>.parquet`` whole (write beside, then ``os.replace``).
  On Windows that replace FAILS while any program has the old file open. A
  viewer left open overnight on the snapshot would break the nightly export.

So each person gets a private SQLite copy, built from the snapshot into their
own local application-data folder (build.py), and the viewer opens only that.
The snapshot is read once per new snapshot, each file for a second or two and
then closed, and never while the snapshot job is mid-write. (Windows refuses
to replace a file while ANY program has it open, whatever sharing it asked
for, so a short read is the only safe read; the snapshot job also waits up to
two minutes for a reader to let go before it swaps a table in --
exporters.refresh_snapshot.swap_in.) The copy is opened immutable and marked
read-only, so nothing can be written through the viewer.
"""
