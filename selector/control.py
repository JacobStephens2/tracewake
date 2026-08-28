"""The Selector's mutable control state.

The pause flag is deliberately not a Journal event. The Journal is the
append-only record downstream of Selector decisions; this singleton row is a
control input the Selector reads immediately before choosing whether to
dispatch.
"""
from __future__ import annotations

import psycopg


def is_paused(conn: psycopg.Connection) -> bool:
    """Whether dispatch is paused."""
    return bool(
        conn.execute(
            "SELECT paused FROM selector.control WHERE singleton = true"
        ).fetchone()[0]
    )


def set_paused(conn: psycopg.Connection, paused: bool) -> None:
    """Set whether future cycles may dispatch."""
    conn.execute(
        "UPDATE selector.control SET paused = %s WHERE singleton = true",
        (paused,),
    )
