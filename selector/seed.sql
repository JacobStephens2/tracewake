-- Fixture data for `selector_staging`, the Attended Preview's Journal
-- (ADR 0016). Applied after schema.sql to a database that is disposable by
-- design; never to `selector`.
--
-- It is not decoration. The live Journal holds no `run.dispatched` and no
-- `run.outcome` rows at all, so no outcome state has ever been rendered by
-- anything - a preview reading real data would show a page with no Run cards
-- and prove nothing about a branch that changed how Run cards look. This file
-- is the only thing that makes every state on /loop visible, which also makes
-- it the thing that catches a branch that broke one.
--
-- Every state the page can render appears below exactly once, and every kind
-- the Selector writes appears in the shape today's writer writes it - the
-- payload key sets are graded against `events.py`'s constructors by
-- `web/tests/test_staging_seed.py`, so a fixture row cannot drift from
-- the writer it impersonates. Adding a state to the page, or a kind to the
-- vocabulary, means adding it here in the same change.
--
-- One consequence worth knowing before you report it as a bug: the status
-- strip's budget cell reads `8 of 4` against this fixture. That is arithmetic
-- rather than breakage. The cap is 4 dispatches per rolling 24 hours and the
-- Selector refuses the fifth, so eight inside one day is unreachable in life -
-- but this file packs every outcome state into the last hours on purpose, and
-- each of them needs its own `run.dispatched`. Spreading them across days to
-- make the cell read plausibly would push most of the Run cards out of the
-- page's read limit, which is the thing the fixture exists to show.
--
-- Idempotent by refusing to double-seed: re-applying to a seeded database is
-- a no-op, and the table is append-only so there is no other way to be.

DO $$
DECLARE
    cycle_id bigint;
    repo     text := 'Educational-Travel-Adventures/tourbot';
BEGIN

IF EXISTS (SELECT 1 FROM journal.events) THEN
    RAISE NOTICE 'journal.events is not empty; seed skipped';
    RETURN;
END IF;

-- 1. A whole cycle: three skips (one of them loud), a pick, a Run that ended
--    green and went to the review queue.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '5 hours', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '5 hours' + interval '2 seconds', 'issue.skipped',
     jsonb_build_object('cycle', cycle_id, 'number', 646,
        'title', 'Nightly sync: retry a failed pull once',
        'url', 'https://github.com/' || repo || '/issues/646',
        'reason', 'blocked-by-open-dependency',
        'detail', '1 open blocking edge(s) on the tracker')),
    (now() - interval '5 hours' + interval '3 seconds', 'issue.skipped',
     jsonb_build_object('cycle', cycle_id, 'number', 648,
        'title', 'Guides: sort the day plan by start time',
        'url', 'https://github.com/' || repo || '/issues/648',
        'reason', 'proposal-open',
        'detail', 'an open pull request closes it: the issue is in flight')),
    (now() - interval '5 hours' + interval '4 seconds', 'issue.skipped',
     jsonb_build_object('cycle', cycle_id, 'number', 596,
        'title', 'Rooming list: import the hotel CSV',
        'url', 'https://github.com/' || repo || '/issues/596',
        'reason', 'missing-section',
        'detail', 'no `Acceptance criteria` section')),
    -- The loud skip's second half: commented on and handed back.
    (now() - interval '5 hours' + interval '5 seconds', 'issue.returned',
     jsonb_build_object('cycle', cycle_id, 'number', 596,
        'title', 'Rooming list: import the hotel CSV',
        'url', 'https://github.com/' || repo || '/issues/596',
        'reason', 'missing-section',
        'detail', 'no `Acceptance criteria` section',
        'added_label', 'needs-info', 'removed_label', 'ready-for-agent')),
    (now() - interval '5 hours' + interval '6 seconds', 'cycle.picked',
     jsonb_build_object('cycle', cycle_id, 'number', 645,
        'title', 'Widen the nightly sync window',
        'url', 'https://github.com/' || repo || '/issues/645',
        'area', 'The nightly sync script', 'check', 'scripts/check.sh')),
    (now() - interval '5 hours' + interval '7 seconds', 'cycle.finished',
     jsonb_build_object('cycle', cycle_id, 'considered', 4,
        'eligible', jsonb_build_array(645), 'picked', 645,
        'skipped', jsonb_build_object('blocked-by-open-dependency', 1,
            'proposal-open', 1, 'missing-section', 1),
        'halted', NULL, 'in_flight', false, 'dispatched_in_window', 0,
        'daily_cap', 4, 'returned', jsonb_build_array(596),
        'dry_run', false)),
    (now() - interval '5 hours' + interval '8 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 645, 'attempt', 1,
        'title', 'Widen the nightly sync window',
        'url', 'https://github.com/' || repo || '/issues/645',
        'branch', 'loop/645-the-nightly-sync-script',
        'task_ref', repo || '#645',
        'area', 'The nightly sync script', 'check', 'scripts/check.sh',
        'kept_progress', NULL)),
    (now() - interval '4 hours' - interval '20 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 645, 'attempt', 1,
        'title', 'Widen the nightly sync window',
        'url', 'https://github.com/' || repo || '/issues/645',
        'branch', 'loop/645-the-nightly-sync-script',
        'task_ref', repo || '#645',
        'ended_by', 'iteration-cap', 'exit', 0,
        'iterations', 5, 'faults', 'none',
        'proposal', 'https://github.com/' || repo || '/pull/701',
        'proposed', 'proposed', 'notified', 'sent',
        'seed', 'seeded', 'criteria', '3')),
    (now() - interval '4 hours' - interval '19 minutes', 'issue.awaiting-review',
     jsonb_build_object('cycle', cycle_id, 'issue', 645, 'attempt', 1,
        'title', 'Widen the nightly sync window',
        'url', 'https://github.com/' || repo || '/issues/645',
        'outcome', 'iteration-cap',
        'label', 'awaiting-review', 'checks', 'green',
        'proposal', 'https://github.com/' || repo || '/pull/701'));

-- 2. A Run that failed with retry budget left: no label swap, still queued.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '4 hours', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '4 hours' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 1,
        'title', 'Profit margin: carry the supplier currency through',
        'url', 'https://github.com/' || repo || '/issues/652',
        'branch', 'loop/652-profit-margin', 'task_ref', repo || '#652',
        'area', 'The margin calculation', 'check', NULL,
        'kept_progress', NULL)),
    (now() - interval '3 hours' - interval '30 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 1,
        'title', 'Profit margin: carry the supplier currency through',
        'url', 'https://github.com/' || repo || '/issues/652',
        'branch', 'loop/652-profit-margin', 'task_ref', repo || '#652',
        'ended_by', 'agent-failed', 'exit', 4,
        'iterations', 1, 'faults', 'agent-failed',
        'proposal', NULL, 'proposed', 'skipped', 'notified', 'sent',
        'seed', 'seeded', 'criteria', '4')),
    (now() - interval '3 hours' - interval '29 minutes', 'issue.retrying',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 1,
        'title', 'Profit margin: carry the supplier currency through',
        'url', 'https://github.com/' || repo || '/issues/652',
        'of', 2, 'outcome', 'agent-failed', 'proposal', NULL));

-- 3. The retry, given up: the budget is spent and the issue goes to a human.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '3 hours', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '3 hours' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 2,
        'title', 'Profit margin: carry the supplier currency through',
        'url', 'https://github.com/' || repo || '/issues/652',
        'branch', 'loop/652-profit-margin', 'task_ref', repo || '#652',
        'area', 'The margin calculation', 'check', NULL,
        'kept_progress', NULL)),
    (now() - interval '2 hours' - interval '40 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 2,
        'title', 'Profit margin: carry the supplier currency through',
        'url', 'https://github.com/' || repo || '/issues/652',
        'branch', 'loop/652-profit-margin', 'task_ref', repo || '#652',
        'ended_by', 'agent-failed', 'exit', 4,
        'iterations', 1, 'faults', 'agent-failed',
        'proposal', NULL, 'proposed', 'skipped', 'notified', 'sent',
        'seed', 'seeded', 'criteria', '4')),
    (now() - interval '2 hours' - interval '39 minutes', 'issue.given-up',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 2,
        'title', 'Profit margin: carry the supplier currency through',
        'url', 'https://github.com/' || repo || '/issues/652',
        'outcome', 'agent-failed', 'proposal', NULL,
        'label', 'ready-for-human'));

-- 4. A Proposal whose checks came back red.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '2 hours', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '2 hours' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 655, 'attempt', 1,
        'title', 'Guide app: stop double-counting the rooming list',
        'url', 'https://github.com/' || repo || '/issues/655',
        'branch', 'loop/655-rooming-list', 'task_ref', repo || '#655',
        'area', 'The rooming list export', 'check', NULL,
        'kept_progress', NULL)),
    (now() - interval '1 hour' - interval '35 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 655, 'attempt', 1,
        'title', 'Guide app: stop double-counting the rooming list',
        'url', 'https://github.com/' || repo || '/issues/655',
        'branch', 'loop/655-rooming-list', 'task_ref', repo || '#655',
        'ended_by', 'iteration-cap', 'exit', 0,
        'iterations', 5, 'faults', 'none',
        'proposal', 'https://github.com/' || repo || '/pull/702',
        'proposed', 'proposed', 'notified', 'sent',
        'seed', 'seeded', 'criteria', '2')),
    (now() - interval '1 hour' - interval '34 minutes', 'issue.handed-to-human',
     jsonb_build_object('cycle', cycle_id, 'issue', 655, 'attempt', 1,
        'title', 'Guide app: stop double-counting the rooming list',
        'url', 'https://github.com/' || repo || '/issues/655',
        'outcome', 'iteration-cap',
        'label', 'ready-for-human', 'checks', 'red',
        'failing', jsonb_build_array('phpunit', 'lint'),
        'proposal', 'https://github.com/' || repo || '/pull/702'));

-- 5. A dispatch that started no Run at all.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '1 hour', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '1 hour' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 660, 'attempt', 1,
        'title', 'Retire the legacy invoice PDF path',
        'url', 'https://github.com/' || repo || '/issues/660',
        'branch', 'loop/660-invoice-pdf', 'task_ref', repo || '#660',
        'area', 'Invoice rendering', 'check', NULL,
        'kept_progress', NULL)),
    (now() - interval '1 hour' + interval '9 seconds', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 660, 'attempt', 1,
        'title', 'Retire the legacy invoice PDF path',
        'url', 'https://github.com/' || repo || '/issues/660',
        'branch', 'loop/660-invoice-pdf', 'task_ref', repo || '#660',
        'ended_by', 'dispatch-failed',
        'error', 'ssh: connect to host loop.etadventures.com port 22: no route to host'));

-- 6. The Run in flight: dispatched, no outcome. This is also the Selector's
--    own in-flight lock - the next cycle stands halted on it (case 7). The
--    watcher's rows ride with it: the Contract read once, and an Iteration
--    per poll - the only sign of life a Run gives off before it ends.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '30 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '30 minutes' + interval '2 seconds', 'cycle.picked',
     jsonb_build_object('cycle', cycle_id, 'number', 661,
        'title', 'Traveler search: match on the preferred name too',
        'url', 'https://github.com/' || repo || '/issues/661',
        'area', 'Traveler search', 'check', NULL)),
    (now() - interval '30 minutes' + interval '3 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 661, 'attempt', 1,
        'title', 'Traveler search: match on the preferred name too',
        'url', 'https://github.com/' || repo || '/issues/661',
        'branch', 'loop/661-traveler-search', 'task_ref', repo || '#661',
        'area', 'Traveler search', 'check', NULL, 'kept_progress', NULL)),
    -- The Contract the watcher read out of the box's Progress Log a minute
    -- after the dispatch (#162). Seeded here because it is the current-Run
    -- panel's contract card, and an Attended Preview of a branch that changes
    -- that card has nothing to show without it.
    (now() - interval '29 minutes', 'run.contract',
     jsonb_build_object('cycle', cycle_id, 'issue', 661, 'attempt', 1,
        'branch', 'loop/661-traveler-search', 'task_ref', repo || '#661',
        'run_started', to_char(now() - interval '30 minutes',
                               'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'contract', jsonb_build_array(
            'Iterations per Run: 5',
            'Iteration wall clock: 900s',
            'Turns per Iteration: 100',
            'Run wall clock: 5400s',
            'Consecutive No-op Iterations that abort: 2',
            'Completion Promise: recorded, never terminal',
            'Agent command: /home/loop/loop/agents/claude.sh',
            'Discipline skills: /tdd for code work, /diagnosing-bugs for '
            || 'something broken or slow, /code-review before every commit'))),
    -- Two Iterations so far: one that committed, one no-op. What the
    -- in-flight card shows while the operator watches.
    (now() - interval '24 minutes', 'run.iteration',
     jsonb_build_object('cycle', cycle_id, 'issue', 661, 'attempt', 1,
        'branch', 'loop/661-traveler-search', 'task_ref', repo || '#661',
        'iteration', 1,
        'started', to_char(now() - interval '29 minutes',
                           'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'run_started', to_char(now() - interval '30 minutes',
                               'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'agent_exit', 0, 'exit_note', NULL, 'turn_bound', 100,
        'noop', false, 'head_before', '3f6b2a91c4d8',
        'head_after', '9e1d4c72ab05', 'promise', NULL, 'dirty', false)),
    (now() - interval '18 minutes', 'run.iteration',
     jsonb_build_object('cycle', cycle_id, 'issue', 661, 'attempt', 1,
        'branch', 'loop/661-traveler-search', 'task_ref', repo || '#661',
        'iteration', 2,
        'started', to_char(now() - interval '23 minutes',
                           'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'run_started', to_char(now() - interval '30 minutes',
                               'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'agent_exit', 0, 'exit_note', NULL, 'turn_bound', 100,
        'noop', true, 'head_before', '9e1d4c72ab05',
        'head_after', '9e1d4c72ab05', 'promise', NULL, 'dirty', false));

-- 7. A cycle that picked nothing, because the Run above is in flight. Timed
--    after that Run's dispatch, so the history the fixture tells is
--    consistent: the lock exists before a cycle halts on it.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '28 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', true))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '28 minutes' + interval '2 seconds', 'cycle.finished',
     jsonb_build_object('cycle', cycle_id, 'considered', 3,
        'eligible', jsonb_build_array(661, 663), 'picked', NULL,
        'skipped', jsonb_build_object('attempts-exhausted', 1),
        'halted', 'run-in-flight', 'in_flight', true,
        'dispatched_in_window', 3, 'daily_cap', 4,
        'returned', jsonb_build_array(), 'dry_run', true));

-- 8. A cycle that failed outright: the tracker could not be read, so there
--    was no queue to reason about.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '25 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '25 minutes' + interval '4 seconds', 'cycle.failed',
     jsonb_build_object('cycle', cycle_id,
        'error', 'tracker command exited 1: gh: API rate limit exceeded'));

-- 9. A loud skip the tracker refused: the Selector could not hand the issue
--    back, so the page must show the attempt AND its failure rather than the
--    tidy "commented, swapped to needs-info" of case 1.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '20 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '20 minutes' + interval '2 seconds', 'issue.skipped',
     jsonb_build_object('cycle', cycle_id, 'number', 663,
        'title', 'Suppliers: dedupe the contact list',
        'url', 'https://github.com/' || repo || '/issues/663',
        'reason', 'missing-section',
        'detail', 'no `Acceptance criteria` section')),
    (now() - interval '20 minutes' + interval '3 seconds', 'issue.return-failed',
     jsonb_build_object('cycle', cycle_id, 'number', 663,
        'title', 'Suppliers: dedupe the contact list',
        'url', 'https://github.com/' || repo || '/issues/663',
        'reason', 'missing-section',
        'detail', 'no `Acceptance criteria` section',
        'added_label', 'needs-info', 'removed_label', 'ready-for-agent',
        'error', 'GitHub refused the label swap: resource not accessible'));

-- 10. A Proposal whose checks never settled inside the Selector's wait. It is
--     NOT the same card as red checks: nothing is known to be wrong, so the
--     page names no failing check and says the wait ran out. The watcher's
--     one-row failure rides this Run: its Progress Log could not be read, so
--     the card carries the watch error instead of Iterations.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '15 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '15 minutes' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 658, 'attempt', 1,
        'title', 'Invoices: show the deposit line before the balance',
        'url', 'https://github.com/' || repo || '/issues/658',
        'branch', 'loop/658-deposit-line', 'task_ref', repo || '#658',
        'area', 'Invoice rendering', 'check', NULL, 'kept_progress', NULL)),
    (now() - interval '14 minutes', 'run.watch-failed',
     jsonb_build_object('cycle', cycle_id, 'issue', 658, 'attempt', 1,
        'branch', 'loop/658-deposit-line', 'task_ref', repo || '#658',
        'error', 'progress command exited 255: ssh: connection reset')),
    (now() - interval '10 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 658, 'attempt', 1,
        'title', 'Invoices: show the deposit line before the balance',
        'url', 'https://github.com/' || repo || '/issues/658',
        'branch', 'loop/658-deposit-line', 'task_ref', repo || '#658',
        'ended_by', 'iteration-cap', 'exit', 0,
        'iterations', 4, 'faults', 'none',
        'proposal', 'https://github.com/' || repo || '/pull/703',
        'proposed', 'proposed', 'notified', 'sent',
        'seed', 'seeded', 'criteria', '2')),
    (now() - interval '9 minutes', 'issue.handed-to-human',
     jsonb_build_object('cycle', cycle_id, 'issue', 658, 'attempt', 1,
        'title', 'Invoices: show the deposit line before the balance',
        'url', 'https://github.com/' || repo || '/issues/658',
        'outcome', 'iteration-cap',
        'label', 'ready-for-human', 'checks', 'pending',
        'failing', jsonb_build_array(),
        'proposal', 'https://github.com/' || repo || '/pull/703'));

-- 11. A route the tracker refused: the Run ended green, and GitHub would not
--     take the label swap - so the Journal keeps the outcome row (written
--     first, deliberately) and the bookkeeping failure beside it.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '12 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent',
        'allowlist', jsonb_build_array('JacobStephens2'), 'daily_cap', 4,
        'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '12 minutes' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 667, 'attempt', 1,
        'title', 'Payments: name the card network on the receipt',
        'url', 'https://github.com/' || repo || '/issues/667',
        'branch', 'loop/667-card-network', 'task_ref', repo || '#667',
        'area', 'Receipt rendering', 'check', NULL, 'kept_progress', NULL)),
    (now() - interval '11 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 667, 'attempt', 1,
        'title', 'Payments: name the card network on the receipt',
        'url', 'https://github.com/' || repo || '/issues/667',
        'branch', 'loop/667-card-network', 'task_ref', repo || '#667',
        'ended_by', 'iteration-cap', 'exit', 0,
        'iterations', 3, 'faults', 'none',
        'proposal', 'https://github.com/' || repo || '/pull/704',
        'proposed', 'proposed', 'notified', 'sent',
        'seed', 'seeded', 'criteria', '1')),
    (now() - interval '10 minutes' - interval '30 seconds', 'issue.route-failed',
     jsonb_build_object('cycle', cycle_id, 'issue', 667, 'attempt', 1,
        'title', 'Payments: name the card network on the receipt',
        'url', 'https://github.com/' || repo || '/issues/667',
        'outcome', 'iteration-cap',
        'label', 'awaiting-review', 'checks', 'green',
        'proposal', 'https://github.com/' || repo || '/pull/704',
        'error', 'GitHub refused the label swap: secondary rate limit'));

-- 12. A timer firing that stood down: another cycle held the advisory lock,
--     so this one journaled the one row that carries no cycle id and exited 0.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '5 minutes', 'cycle.skipped',
     jsonb_build_object('reason', 'cycle-in-progress'));

-- 13. The box, as the status strip's box card renders it (#156). A cycle reads
--     the box over SSH once per cycle and journals what it found, so without a
--     row here a preview shows `not read yet` and proves nothing about a branch
--     that changed the card. The rows ride the newest seeded cycle's id.
--
--     Both states are seeded, oldest first, because the card shows the NEWEST
--     of either kind - deliberately, so an outage cannot be hidden behind last
--     week's good read. That means only `box.observed` renders here; to review
--     the outage state instead, swap the two intervals below rather than
--     deleting one, so the fixture keeps covering both.
--
--     `guest_template` is null on purpose: the box's copy of the Loop is placed
--     by an ansible apply rather than by a merge, so a fact the box does not
--     report is the ordinary case, and `not reported` is a state of the card.
--     The credential expiry is a few hours out, so the card renders the
--     remaining-time headline a preview exists to review (#260).
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '45 minutes', 'box.unreachable',
     jsonb_build_object('cycle', cycle_id,
        'error', 'ssh: connect to host loop.etadventures.com port 22: '
                 || 'Connection timed out')),
    (now() - interval '15 minutes', 'box.observed',
     jsonb_build_object('cycle', cycle_id,
        'scripts_hash', '78014f98ea8a', 'guest_template', null,
        'agent', 'claude', 'agent_version', '2.1.221 (Claude Code)',
        'credential_expires_at',
        to_char((now() + interval '5 hours') at time zone 'utc',
                'YYYY-MM-DD"T"HH24:MI:SS"Z"')));

-- 14. The write protection over the executed paths, as the guardrail chip
--     renders it (#165). Journaled by the same part of the cycle as the box
--     card above, so without a row here a preview shows `not checked yet`.
--     Same newest-of-either-kind rule as the box card, so the unreadable
--     state is seeded older and the green reading renders.
--
--     Green, because green is the state a preview is nearly always reviewing:
--     the red states are one payload away (drop a rule from `rules`, or put a
--     path in `unreviewed` with a `detail` to match, and set `protected`
--     false), and both are covered by the dashboard suite rather than by this
--     fixture.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '40 minutes', 'guardrail.unreadable',
     jsonb_build_object('cycle', cycle_id,
        'error', 'gh: connect to api.github.com: network is unreachable')),
    (now() - interval '15 minutes', 'guardrail.observed',
     jsonb_build_object('cycle', cycle_id,
        'ref', 'master', 'ref_head', '44a596d0cbb3',
        'rules', jsonb_build_array('deletion', 'non_fast_forward',
                                   'pull_request'),
        'paths', jsonb_build_array('loop',
                                   'selector',
                                   'deploy/systemd/tracewake-selector-cycle.service',
                                   'deploy/systemd/tracewake-selector-cycle.timer',
                                   'deploy/systemd/tracewake-selector-notifier.service'),
        'unreviewed', jsonb_build_array(),
        'protected', true, 'detail', null));

END $$;
