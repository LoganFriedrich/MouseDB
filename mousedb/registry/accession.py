"""
Accession number generation for the Figure Registry.

Format: FIG-YYYYMMDD-XXXX where XXXX is a zero-padded sequential counter per day.
Thread-safe via database serialization (SQLite write lock).
"""

from datetime import datetime

from sqlalchemy import text


def generate_accession(session) -> str:
    """Generate the next accession number for today.

    Queries figure_records for the max accession with today's date prefix,
    increments the counter. Thread-safe via database serialization.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        Active database session (must be inside a transaction).

    Returns
    -------
    str
        Accession in "FIG-YYYYMMDD-XXXX" format, e.g. "FIG-20260304-0001".
    """
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"FIG-{today}-"

    result = session.execute(
        text(
            "SELECT accession FROM figure_records "
            "WHERE accession LIKE :pattern "
            "ORDER BY accession DESC LIMIT 1"
        ),
        {"pattern": f"{prefix}%"},
    ).fetchone()

    if result is None:
        next_num = 1
    else:
        # Parse the counter portion after the last hyphen
        last_accession = result[0]
        counter_str = last_accession.split("-")[-1]
        next_num = int(counter_str) + 1

    return f"{prefix}{next_num:04d}"
