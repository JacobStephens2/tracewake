"""The Host on the Queue Board (#42), at HTTP level.

The sampler is the substitutable seam: tests replace it and assert the page
shows the figures it returned. Runs in flight are not the sampler's - they
come from the Journal's in-flight predicate, the same one Eligibility uses.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app import app
from conftest import column
from host import Sample, SamplerError

client = TestClient(app)

GIB = 1024 ** 3


def host_widget(body: str) -> str:
    """The host strip, cut out of the page.

    Sliced rather than searched whole: "41%" somewhere on the page is not
    "the widget showed what the sampler returned".
    """
    match = re.search(r'<aside class="host"[^>]*>(.*?)</aside>', body, re.DOTALL)
    assert match, "no host widget on the page"
    return match.group(1)


def _sample(**overrides):
    facts = dict(
        cpu_percent=41.0,
        memory_used=2 * GIB,
        memory_total=8 * GIB,
        disk_used=12 * GIB,
        disk_total=77 * GIB,
        disk_path="/var/lib/tracewake",
    )
    facts.update(overrides)
    return Sample(**facts)


def in_flight_cell(body: str) -> str:
    widget = host_widget(body)
    match = re.search(
        r'<div class="cell cell-in-flight">(.*?)</div>', widget, re.DOTALL
    )
    assert match, "no in-flight cell on the host widget"
    return match.group(1)


@pytest.mark.parametrize("path", ["/", "/loop"])
def test_the_queue_board_shows_the_hosts_cpu_memory_and_disk(
    path, db, monkeypatch
):
    monkeypatch.setattr("host.sample", lambda: _sample())

    widget = host_widget(client.get(path).text)

    assert "41%" in widget
    assert "2.0 GiB" in widget
    assert "8.0 GiB" in widget
    assert "12 GiB" in widget
    assert "77 GiB" in widget
    assert "/var/lib/tracewake" in widget


def test_the_figures_ride_the_live_region(db, monkeypatch):
    """The same HTML the SSE swap fetches, so a reload is not required."""
    monkeypatch.setattr("host.sample", lambda: _sample())
    fragment = client.get("/loop/live").text
    assert fragment.lstrip().startswith('<div id="live-region"')
    widget = host_widget(fragment)
    assert "41%" in widget
    assert "2.0 GiB" in widget


def test_a_sampler_failure_degrades_the_widget_not_the_board(
    db, tracker, monkeypatch
):
    tracker.queue("ready-for-agent", [tracker.issue(101)])

    def boom():
        raise SamplerError("cannot read /proc")

    monkeypatch.setattr("host.sample", boom)

    resp = client.get("/loop")
    assert resp.status_code == 200
    assert 'data-host="degraded"' in resp.text
    widget = host_widget(resp.text)
    assert "unknown" in widget
    assert "cannot read /proc" in widget
    assert "#101" in column(resp.text, "eligible")


def test_runs_in_flight_match_the_journal_under_a_run_and_after_its_outcome(
    db, dispatch, monkeypatch
):
    monkeypatch.setattr("host.sample", lambda: _sample())

    assert ">0<" in in_flight_cell(client.get("/loop").text).replace(" ", "")

    dispatch(db, 108)
    assert ">1<" in in_flight_cell(client.get("/loop").text).replace(" ", "")

    import events
    import journal
    with journal.connect() as conn:
        journal.append(conn, *events.run_outcome(
            cycle=None, issue=108, title=None, url=None, task_ref=None,
            attempt=None, branch=None, ended_by="complete", exit=None,
            iterations=None, faults=None, proposal=None, proposed=None,
            notified=None, seed=None, criteria=None,
        ))
    assert ">0<" in in_flight_cell(client.get("/loop").text).replace(" ", "")


def test_two_in_flight_runs_count_as_two(db, dispatch, monkeypatch):
    monkeypatch.setattr("host.sample", lambda: _sample())
    dispatch(db, 108)
    dispatch(db, 109)
    assert ">2<" in in_flight_cell(client.get("/loop").text).replace(" ", "")


def test_the_same_issue_on_two_targets_counts_as_two_runs(db, monkeypatch):
    """Eligibility's lock is per (repo, issue). Unique issue numbers would
    report one Run while two Targets each hold a slot (issue #37)."""
    monkeypatch.setattr("host.sample", lambda: _sample())
    import events
    import journal
    with journal.connect() as conn:
        for repo in ("acme/one", "acme/two"):
            journal.append(conn, *events.run_dispatched(
                cycle=None, issue=108, title=None, url=None,
                task_ref=f"{repo}#108", attempt=None, branch=None,
                area=None, check=None, kept_progress=None,
            ))
    assert ">2<" in in_flight_cell(client.get("/loop").text).replace(" ", "")


def test_a_stale_dispatch_is_not_in_flight(db, dispatch, monkeypatch):
    """Eligibility's predicate, not the Run cards: a dispatch older than the
    stale bound still has a card and no outcome, but it does not hold a slot."""
    monkeypatch.setattr("host.sample", lambda: _sample())
    dispatch(db, 108, hours_ago=5)
    assert ">0<" in in_flight_cell(client.get("/loop").text).replace(" ", "")


def test_the_default_sampler_reads_this_machine():
    import host
    first = host.sample()
    second = host.sample()
    for facts in (first, second):
        assert facts.memory_total > 0
        assert facts.disk_total > 0
        assert facts.memory_used >= 0
        assert facts.disk_used >= 0
        assert 0 <= facts.cpu_percent <= 100
        assert facts.disk_path
