"""The pause flag at its database boundary."""
import psycopg

import control
import testdb


def test_schema_reapplication_does_not_resume_a_paused_selector(db):
    """Ansible reapplies schema.sql. That must not turn an operator control
    back into its default while the page still says the Selector is paused."""
    with psycopg.connect(db, autocommit=True) as conn:
        control.set_paused(conn, True)
        conn.execute(testdb.SCHEMA.read_text())
        assert control.is_paused(conn) is True
