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
