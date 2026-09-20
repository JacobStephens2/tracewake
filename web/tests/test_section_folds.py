"""Foldable sections on `/`: every headed panel hides and displays like The queue.

The queue already folds at its heading (`details.fold`, state kept in
localStorage by loop-ui.js). The other headed sections on the home page
did not, so a reader who wanted the overview had to scroll past Targets,
the Host, the Runs, and the raw event table. Same control, same markup,
same stored choice that survives a live-region swap.
"""
import re

from fastapi.testclient import TestClient

import journal
from app import app

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


def test_each_headed_section_on_home_folds_like_the_queue(db, dispatch):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
    dispatch(db, 646, outcome="complete")

    expected = {
        "targets": "Targets",
        "host": "The host",
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


def _fold_open(body: str, key: str) -> bool:
    """Whether a fold's markup starts open.

    The attribute is the default a first visit sees. loop-ui.js may
    override it from localStorage after paint; these tests read the
    HTML the server sent.
    """
    match = re.search(rf'<details\b([^>]*\bdata-fold="{key}"[^>]*)>', body)
    assert match, f"no fold named {key!r}"
    return bool(re.search(r"(?:^|\s)open(?:\s|=|>|$)", match.group(1)))


def test_the_targets_section_starts_closed(db):
    """Configuration, not the reason the page is open.

    The queue, the Host, and the Runs start open so the overview is
    the page. Targets is a list the operator changes occasionally;
    starting it open pushes the queue down by a form. Closed on a
    first visit; a stored choice still wins after paint.
    """
    body = client.get("/").text
    assert _fold_open(body, "targets") is False
    assert _fold_open(body, "queue") is True
