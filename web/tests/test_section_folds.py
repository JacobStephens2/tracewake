"""Foldable sections on `/`: every headed panel hides and displays like The queue.

The queue already folds at its heading (`details.fold`). The other headed
sections on the home page did not, so a reader who wanted the overview had
to scroll past Targets, the Host, the Runs, and the raw event table. Same
control, same markup. The visitor's last open/shut is a cookie the server
reads, so a reload and a live-region swap both come back the way they left.
"""
import re

from fastapi.testclient import TestClient

import journal
from app import FOLD_COOKIE, app

client = TestClient(app)

FOLD = re.compile(
    r"<details\b([^>]*)>\s*<summary>\s*<h2>([^<]+)</h2>\s*</summary>",
)


def folds_on(body: str) -> dict[str, str]:
    """data-fold key → heading, in document order.

    The heading is the control: a fold whose h2 sits outside the summary
    is not the same control The queue already is.
    """
    found = {}
    for attrs, heading in FOLD.findall(body):
        key = re.search(r'\bdata-fold="([^"]+)"', attrs)
        assert key, f"{heading!r} is a details but has no data-fold"
        assert re.search(r'\bclass="[^"]*\bfold\b', attrs), heading
        found[key.group(1)] = heading
    return found


def fold_open(body: str) -> dict[str, bool]:
    """data-fold key → whether the details carries the open attribute."""
    found = {}
    for attrs, heading in FOLD.findall(body):
        key = re.search(r'\bdata-fold="([^"]+)"', attrs)
        assert key, f"{heading!r} is a details but has no data-fold"
        found[key.group(1)] = bool(re.search(r"(?:^|\s)open(?:\s|$)", attrs))
    return found


def test_each_headed_section_on_home_folds_like_the_queue(db, dispatch):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
    dispatch(db, 646, outcome="complete")

    expected = {
        "targets": "Targets",
        "strip": "The status strip",
        "host": "The host",
        "microvms": "MicroVMs",
        "queue": "The queue",
        "runs": "Runs",
        "cycles": "Cycles",
        "events": "Every event",
    }
    body = client.get("/").text
    assert folds_on(body) == expected

    # The live fragment is what a Journal row swaps in. Targets stays on
    # the shell (configuration, not a replay); everything else has to be
    # in the fragment or a swap would drop the fold.
    live = folds_on(client.get("/loop/live").text)
    assert "targets" not in live
    assert live == {k: v for k, v in expected.items() if k != "targets"}


def test_the_targets_section_starts_closed(db):
    """Configuration, not the reason the page is open.

    The queue, the Host, and the Runs start open so the overview is
    the page. Targets is a list the operator changes occasionally;
    starting it open pushes the queue down by a form. Closed on a
    first visit; a stored choice is the markup on the next load (#123).
    """
    home = fold_open(client.get("/").text)
    assert home["targets"] is False
    assert home["queue"] is True


def test_a_stored_fold_choice_is_the_markup_on_the_next_load(db, dispatch):
    """localStorage cannot do this: the server never sees it, so a reload
    and an hx-get both come back with the markup defaults (#123).
    """
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
    dispatch(db, 646, outcome="complete")

    browser = TestClient(app)
    browser.cookies.set(
        FOLD_COOKIE, "targets:open|strip:closed|queue:closed|cycles:open"
    )
    home = fold_open(browser.get("/").text)
    assert home["targets"] is True
    assert home["strip"] is False
    assert home["queue"] is False
    assert home["cycles"] is True
    # Unmentioned sections keep the markup default.
    assert home["host"] is True
    assert home["microvms"] is True
    assert home["runs"] is True
    assert home["events"] is True

    live = fold_open(browser.get("/loop/live").text)
    assert "targets" not in live
    assert live["strip"] is False
    assert live["queue"] is False
    assert live["cycles"] is True


def test_a_malformed_fold_cookie_leaves_the_markup_defaults(db, dispatch):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
    dispatch(db, 646, outcome="complete")

    browser = TestClient(app)
    browser.cookies.set(
        FOLD_COOKIE,
        'queue:yes|cycles:closed"><script>|unknown:closed|targets:closed',
    )
    body = browser.get("/").text
    home = fold_open(body)
    assert home["queue"] is True
    assert home["cycles"] is False
    assert home["targets"] is False
    assert 'closed"><script>' not in body
