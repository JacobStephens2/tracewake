#!/usr/bin/env python3
"""The Selector's cycle: read the tracker, apply Eligibility, journal the lot.

One cycle is: fetch the labeled queue through the tracker command, decide for
every issue in it whether it is Eligible and - when it is not - exactly why,
order what survives lowest-first, apply the caps, and append the whole of that
reasoning to the Selector Journal. The pick is the cycle's answer; the Journal
is its argument for the answer, which is what makes "why not #646 yesterday?"
a question with a recorded reply (spec #151, story 22).

Deterministic code, never an agent: no model output executes here, which is
what lets the Selector live on the VM that holds production credentials (ADR
0014).

A cycle has two modes and one body of reasoning. `--dry-run` reaches the
tracker command and the owner-wide search, both read-only, and writes exactly
one thing, the Journal. Without it the same reasoning is followed by acting on
it (issue #154): the pick is dispatched - branch, Seeding, push, the Run
started on the box - and an issue skipped for a missing section is returned to
the operator with a comment and a `needs-info` swap rather than quietly passed
over. The unenrolled-Target warning (issue #39) is a Journal row in both
modes; mail is the notifier's, and only for a live newly appearing gap.

The two modes share every line of the deciding, so a dry-run is the cycle that
would have happened and not a separate approximation of one.

Everything the cycle reaches is a substitutable command (the Loop's *_COMMAND
convention, ADR 0004), which is also the seam the offline suite drives:
tests script canned queue states through SELECTOR_TRACKER_COMMAND and read the
Journal back.

Usage:

    cycle.py [--dry-run] [--target owner/name]

What it works comes from configuration and never from a default (issue #3).
The **instance** is environment - the tracker command, the box, the Journal,
the Seeding command, the issue command; the **targets** are stanzas in
`targets.toml`, one per repository, each carrying its own labels, labeler
allowlist, checkouts, repository token, guest image, review cap and landing
mode. See README.md. INSTALL.md shows the Single-Host shape of both files.

Without `--target` every declared target is worked, each with its own
`cycle.started`/`cycle.finished` pair: a second repository is a second stanza,
not a second controller. With `SELECTOR_DRAIN_CONCURRENCY` greater than 1,
Dispatches on different Targets overlap up to that cap; within a Target they
stay serial. A required value that is absent stops everything at preflight,
naming the value, before the tracker is read.

Exit codes:

    0  the cycle ran and journaled its reasoning (with or without a pick), and
       anything it acted on succeeded. A Run that ended on a bound is a
       success here: which bound ended it is the Run's business, and the
       Termination Contract working is not the Selector failing.
    1  the cycle could not run, or something it tried to do failed - a tracker
       that did not answer, a Seeding refusal, a box that started no Run, a
       label swap GitHub rejected. Non-zero on purpose: the timer's OnFailure
       pages the operator, because a dead Selector must never look like a
       quiet queue (story 31).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatch  # noqa: E402
import events  # noqa: E402
import journal  # noqa: E402
import targets  # noqa: E402
from drain import CycleFailed, CycleResult, run_cycle  # noqa: E402

HERE = Path(__file__).resolve().parent

# One cycle at a time, whoever started it. A Postgres advisory lock on the
# Journal connection rather than a lock file, for two reasons: the Journal is
# already the one thing every cycle holds open, and a lock the DATABASE owns
# is released by the connection dying - a lock file left behind by a killed
# cycle would wedge the timer until somebody noticed and deleted it.
#
# It matters because overlap is the normal case here, not the exceptional one:
# the timer fires every thirty minutes and a dispatch holds the process for as
# long as the Run lasts, which the Termination Contract bounds at ninety
# minutes. Two cycles reasoning at once would read the same spend, and could
# pick and dispatch the same issue twice.
#
# The in-flight lock in `Spend` does NOT cover this. That one is journaled and
# is about Runs; this one is about processes, and is what stops a second cycle
# before it has read anything at all.
CYCLE_LOCK_KEY = 0x5E1EC7


def fetch_owner_search(owner: str, label: str) -> list[dict]:
    """Handover-labeled issues across an owner's repositories.

    One owner-wide search per Cycle (issue #39). Substitutable like the
    per-target tracker command, and deliberately *not* overlaid with a
    target's environment: a per-target token must not be the credential that
    lists other repositories.
    """
    command = os.environ.get(
        "SELECTOR_SEARCH_COMMAND",
        str(HERE / "search-sources" / "github.sh"),
    )
    try:
        completed = subprocess.run(
            [command, owner, label],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise CycleFailed(f"search command could not be run: {exc}") from exc
    if completed.returncode:
        raise CycleFailed(
            f"search command exited {completed.returncode}: "
            f"{completed.stderr.strip() or 'no output'}"
        )
    try:
        payload = json.loads(completed.stdout)
        return list(payload["issues"])
    except (ValueError, KeyError, TypeError) as exc:
        raise CycleFailed(
            f"search command did not return a labeled-issue list: {exc}"
        ) from exc


def _handover_label(declared: tuple[targets.Target, ...]) -> str:
    """The label the owner-wide search looks for.

    Unenrolled repositories have no stanza, so they cannot declare a rename.
    When every declared Target shares one ready label, that is the Handover
    this instance uses; otherwise the product vocabulary.
    """
    labels = {target.labels.ready for target in declared}
    if len(labels) == 1:
        return labels.pop()
    return targets.DEFAULT_LABELS["ready"]


def last_live_unenrolled_repos(conn: psycopg.Connection) -> set[str]:
    """Repos in the most recent non-dry-run unenrolled warning.

    Deduplication is keyed off the Journal (issue #39): a standing gap
    journals again with `new=[]`, and a dry-run must not eat the live
    notification.
    """
    row = conn.execute(
        "SELECT payload FROM journal.events"
        " WHERE kind = %s"
        "   AND COALESCE(payload->>'dry_run', 'false') NOT IN ('true', 'True')"
        " ORDER BY id DESC LIMIT 1",
        (events.TARGET_UNENROLLED,),
    ).fetchone()
    if not row:
        return set()
    payload = row[0] or {}
    repos = payload.get("repos") or []
    return {
        str(entry["repo"])
        for entry in repos
        if isinstance(entry, dict) and entry.get("repo")
    }


def group_unenrolled(issues: list[dict], declared: set[str]) -> list[dict]:
    """The gap: labeled issues whose repository is not a declared Target."""
    grouped: dict[str, list[dict]] = {}
    for record in issues:
        repo = record.get("repo")
        if not repo or repo in declared:
            continue
        grouped.setdefault(repo, []).append(
            {
                "number": record.get("number"),
                "title": record.get("title"),
                "url": record.get("url"),
            }
        )
    gap = []
    for repo in sorted(grouped):
        found = grouped[repo]
        found.sort(
            key=lambda item: int(item["number"])
            if str(item.get("number") or "").isdigit()
            else 0
        )
        gap.append({"repo": repo, "issues": found})
    return gap


def observe_unenrolled(
    conn: psycopg.Connection,
    instance: targets.Instance,
    declared: tuple[targets.Target, ...],
    *,
    dry_run: bool,
) -> None:
    """Journal the unenrolled-Target gap, or the empty set when one closed.

    Warn-only: this function reads the tracker and writes the Journal. It
    does not comment, relabel, or edit any Target configuration.
    """
    label = _handover_label(declared)
    found = fetch_owner_search(instance.search_owner, label)
    gap = group_unenrolled(found, {target.repo for target in declared})
    seen = last_live_unenrolled_repos(conn)
    if not gap:
        # Record the empty set so a later reappearance is new, rather than
        # matching the last non-empty row and staying silent. A gap that
        # was never open is not journaled.
        if not seen:
            return
        journal.append(
            conn,
            *events.target_unenrolled(
                owner=instance.search_owner,
                label=label,
                repos=[],
                new=[],
                dry_run=dry_run,
            ),
        )
        return
    new = [entry["repo"] for entry in gap if entry["repo"] not in seen]
    journal.append(
        conn,
        *events.target_unenrolled(
            owner=instance.search_owner,
            label=label,
            repos=gap,
            new=new,
            dry_run=dry_run,
        ),
    )


def _run_targets(
    conn: psycopg.Connection,
    configs: tuple[targets.Config, ...],
    *,
    dry_run: bool,
) -> tuple[list[CycleResult | None], CycleFailed | None]:
    """Work every target. Sequential when K is 1; concurrent otherwise.

    One Cycle per target, each with its own `cycle.started` / `cycle.finished`
    pair. Parallelism is K concurrent Dispatches across those Cycles, still
    serial within a Target (issue #37). A target that fails does not cancel
    in-flight Runs on the others; the first failure is returned after they
    finish so the caller can page.
    """
    if not configs:
        return [], None
    concurrency = configs[0].drain_concurrency
    parallel = (not dry_run) and concurrency > 1 and len(configs) > 1
    if not parallel:
        # One cycle per target, each with its own `cycle.started` and
        # `cycle.finished` pair. A second target is a second stanza and a
        # second pass here - not a second controller, a second timer or a
        # second Journal (issue #3).
        #
        # A target that fails ends the whole invocation, and the targets
        # after it are not worked. Deliberately: a cycle that failed exits
        # non-zero and the unit's OnFailure pages, and carrying on to the
        # next target would turn one page into a cycle that reports both a
        # failure and a success. The timer fires again in thirty minutes, so
        # what a failed first target costs the second is one cycle, not its
        # queue.
        return [
            run_cycle(
                conn,
                config,
                dispatch.DispatchConfig.for_target(config.target),
                dry_run=dry_run,
            )
            for config in configs
        ], None

    slots = threading.BoundedSemaphore(concurrency)
    summaries: list[CycleResult | None] = [None] * len(configs)
    errors: list[CycleFailed] = []

    def run_one(index: int, config: targets.Config) -> None:
        try:
            with journal.connect() as target_conn:
                summaries[index] = run_cycle(
                    target_conn,
                    config,
                    dispatch.DispatchConfig.for_target(config.target),
                    dry_run=dry_run,
                    slots=slots,
                )
        except CycleFailed as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(configs)
    ) as pool:
        futs = [
            pool.submit(run_one, i, config)
            for i, config in enumerate(configs)
        ]
        for fut in concurrent.futures.as_completed(futs):
            fut.result()
    return summaries, (errors[0] if errors else None)


def _report(summary: CycleResult) -> None:
    print(f"considered   {summary.considered}")
    print(f"eligible     {summary.eligible or 'none'}")
    for reason, count in sorted(summary.skipped.items()):
        print(f"  skipped    {count} x {reason}")
    if summary.returned:
        print(f"returned     {summary.returned} (commented, swapped to needs-info)")
    if summary.return_failures:
        print(f"  FAILED     {summary.return_failures} issue(s) could not be returned")
    if summary.updated_proposals:
        print(f"proposals    {', '.join(str(p) for p in summary.updated_proposals)} updated")
    if summary.reconciled_proposals:
        print(f"reconciled   {', '.join(str(p) for p in summary.reconciled_proposals)} reconciled")
    if summary.reconcile_failures:
        print(f"  FAILED     {', '.join(str(p) for p in summary.reconcile_failures)} proposal(s) could not be reconciled")
    if summary.dispatches:
        print(f"dispatches   {', '.join(f'#{n}' for n in summary.dispatches)}")
    elif summary.picked:
        suffix = " (dry run - not dispatched)" if summary.dry_run else ""
        print(f"pick         #{summary.picked}{suffix}")
    else:
        print(f"pick         none ({summary.halted})")
    outcome = summary.outcome
    if outcome:
        print(f"branch       {outcome['branch']}")
        print(
            f"run          {outcome['ended_by']}"
            f" (exit {outcome.get('exit', '?')},"
            f" {outcome.get('iterations', '?')} iteration(s),"
            f" faults {outcome.get('faults', '?')})"
        )
        print(f"proposal     {outcome.get('proposal') or 'none'}")
    if summary.route:
        print(f"routed       {summary.route}")
    print(
        f"budget       {summary.awaiting_review}/{summary.review_cap}"
        f" awaiting review"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One Selector cycle: pick the lowest eligible labeled issue,"
        " and unless --dry-run, dispatch it."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="reason and journal; dispatch nothing and write nothing to the"
        " tracker. The cycle that would have happened.",
    )
    parser.add_argument(
        "--target",
        metavar="OWNER/NAME",
        help="work only the target declared with this repo. Without it every"
        " target in the targets file is worked, in the order it declares"
        " them.",
    )
    args = parser.parse_args(argv)

    try:
        with journal.connect() as conn:
            if not conn.execute(
                "SELECT pg_try_advisory_lock(%s)", (CYCLE_LOCK_KEY,)
            ).fetchone()[0]:
                # Not a failure, and deliberately exit 0: under a
                # thirty-minute timer and a ninety-minute Run this happens two
                # or three times per Run, and a timer whose OnFailure paged
                # for it would page for the Selector working.
                journal.append(
                    conn, *events.cycle_skipped(reason="cycle-in-progress")
                )
                print("cycle.py: another cycle is running; this one stood down")
                return 0

            # Preflight (issue #3). Before the tracker is read and before
            # anything is dispatched: an instance missing a required value
            # stops here, with the value named, rather than part-way through
            # a cycle that has already commented on somebody's issue. It is
            # journaled as well as printed, because a cycle that refused to
            # start is exactly the silence story 31 asks to be paged for.
            try:
                configs = targets.Config.load(args.target)
            except targets.NotConfigured as exc:
                journal.append(
                    conn, *events.cycle_failed(cycle=None, error=str(exc))
                )
                raise CycleFailed(str(exc)) from exc

            # One owner-wide search per Cycle, before any per-target drain,
            # so a Handover on an unenrolled repository cannot vanish
            # silently while the declared Targets are worked (issue #39).
            # `--target` narrows the drain, not the enrollment set.
            try:
                observe_unenrolled(
                    conn,
                    targets.Instance.from_env(),
                    targets.load(),
                    dry_run=args.dry_run,
                )
            except CycleFailed as exc:
                journal.append(
                    conn, *events.cycle_failed(cycle=None, error=str(exc))
                )
                raise

            failures = 0
            summaries, target_error = _run_targets(
                conn, configs, dry_run=args.dry_run
            )
            for config, summary in zip(configs, summaries):
                if summary is None:
                    continue
                if len(configs) > 1:
                    print(f"target       {config.task_repo}")
                _report(summary)
                failures += summary.return_failures
            if target_error is not None:
                raise target_error
    except CycleFailed as exc:
        print(f"cycle.py: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"cycle.py: journal unavailable: {exc}", file=sys.stderr)
        return 1
    # A Run that ended on a bound is not a Selector failure; an issue the
    # Selector could not hand back is. Story 31 asks for the Selector's own
    # failures to page, and a comment or a label swap GitHub refused means the
    # issue is still sitting in the queue with nothing on it saying why.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
