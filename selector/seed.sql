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
-- Every state the page can render appears below exactly once. Adding a state
-- to the page means adding it here in the same change.
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
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '5 hours' + interval '2 seconds', 'issue.skipped',
     jsonb_build_object('cycle', cycle_id, 'number', 646,
        'url', 'https://github.com/' || repo || '/issues/646',
        'reason', 'blocked-by-open-dependency',
        'detail', '1 open blocking edge(s) on the tracker')),
    (now() - interval '5 hours' + interval '3 seconds', 'issue.skipped',
     jsonb_build_object('cycle', cycle_id, 'number', 648,
        'url', 'https://github.com/' || repo || '/issues/648',
        'reason', 'proposal-open',
        'detail', 'an open pull request closes it: the issue is in flight')),
    (now() - interval '5 hours' + interval '4 seconds', 'issue.skipped',
     jsonb_build_object('cycle', cycle_id, 'number', 596,
        'url', 'https://github.com/' || repo || '/issues/596',
        'reason', 'missing-section',
        'detail', 'no `Owning area` section')),
    -- The loud skip's second half: commented on and handed back.
    (now() - interval '5 hours' + interval '5 seconds', 'issue.returned',
     jsonb_build_object('cycle', cycle_id, 'number', 596,
        'reason', 'missing-section',
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
        'halted', NULL, 'dispatched_in_window', 0, 'daily_cap', 4,
        'dry_run', false)),
    (now() - interval '5 hours' + interval '8 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 645, 'attempt', 1,
        'title', 'Widen the nightly sync window',
        'url', 'https://github.com/' || repo || '/issues/645',
        'branch', 'loop/645-the-nightly-sync-script',
        'task_ref', repo || '#645',
        'area', 'The nightly sync script', 'check', 'scripts/check.sh')),
    (now() - interval '4 hours' - interval '20 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 645, 'attempt', 1,
        'outcome', 'iteration-cap', 'ended_by', 'iteration-cap', 'exit', 0,
        'iterations', 5, 'faults', 'none', 'notified', 'sent',
        'proposal', 'https://github.com/' || repo || '/pull/701')),
    (now() - interval '4 hours' - interval '19 minutes', 'issue.awaiting-review',
     jsonb_build_object('cycle', cycle_id, 'issue', 645, 'attempt', 1,
        'label', 'awaiting-review', 'checks', 'green',
        'proposal', 'https://github.com/' || repo || '/pull/701'));

-- 2. A Run that failed with retry budget left: no label swap, still queued.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '4 hours', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '4 hours' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 1,
        'title', 'Profit margin: carry the supplier currency through',
        'url', 'https://github.com/' || repo || '/issues/652',
        'branch', 'loop/652-profit-margin', 'task_ref', repo || '#652',
        'area', 'The margin calculation')),
    (now() - interval '3 hours' - interval '30 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 1,
        'outcome', 'agent-failed', 'ended_by', 'agent-failed', 'exit', 4,
        'iterations', 1, 'faults', 'agent-failed', 'notified', 'sent',
        'proposal', NULL)),
    (now() - interval '3 hours' - interval '29 minutes', 'issue.retrying',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 1,
        'of', 2, 'outcome', 'agent-failed'));

-- 3. The retry, given up: the budget is spent and the issue goes to a human.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '3 hours', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '3 hours' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 2,
        'title', 'Profit margin: carry the supplier currency through',
        'url', 'https://github.com/' || repo || '/issues/652',
        'branch', 'loop/652-profit-margin', 'task_ref', repo || '#652',
        'area', 'The margin calculation')),
    (now() - interval '2 hours' - interval '40 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 2,
        'outcome', 'agent-failed', 'ended_by', 'agent-failed', 'exit', 4,
        'iterations', 1, 'faults', 'agent-failed', 'notified', 'sent',
        'proposal', NULL)),
    (now() - interval '2 hours' - interval '39 minutes', 'issue.given-up',
     jsonb_build_object('cycle', cycle_id, 'issue', 652, 'attempt', 2,
        'label', 'ready-for-human', 'outcome', 'agent-failed'));

-- 4. A Proposal whose checks came back red.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '2 hours', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '2 hours' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 655, 'attempt', 1,
        'title', 'Guide app: stop double-counting the rooming list',
        'url', 'https://github.com/' || repo || '/issues/655',
        'branch', 'loop/655-rooming-list', 'task_ref', repo || '#655',
        'area', 'The rooming list export')),
    (now() - interval '1 hour' - interval '35 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 655, 'attempt', 1,
        'outcome', 'iteration-cap', 'ended_by', 'iteration-cap', 'exit', 0,
        'iterations', 5, 'faults', 'none', 'notified', 'sent',
        'proposal', 'https://github.com/' || repo || '/pull/702')),
    (now() - interval '1 hour' - interval '34 minutes', 'issue.handed-to-human',
     jsonb_build_object('cycle', cycle_id, 'issue', 655, 'attempt', 1,
        'label', 'ready-for-human', 'checks', 'red',
        'failing', jsonb_build_array('phpunit', 'lint'),
        'proposal', 'https://github.com/' || repo || '/pull/702'));

-- 5. A dispatch that started no Run at all.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '1 hour', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '1 hour' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 660, 'attempt', 1,
        'title', 'Retire the legacy invoice PDF path',
        'url', 'https://github.com/' || repo || '/issues/660',
        'branch', 'loop/660-invoice-pdf', 'task_ref', repo || '#660',
        'area', 'Invoice rendering')),
    (now() - interval '1 hour' + interval '9 seconds', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 660, 'attempt', 1,
        'outcome', 'dispatch-failed', 'error', 'ssh: connect to host loop.etadventures.com port 22: no route to host'));

-- 6. A cycle that picked nothing, because a Run is in flight.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '35 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', true))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '35 minutes' + interval '2 seconds', 'cycle.finished',
     jsonb_build_object('cycle', cycle_id, 'considered', 3,
        'eligible', jsonb_build_array(661, 663), 'picked', NULL,
        'skipped', jsonb_build_object('attempts-exhausted', 1),
        'halted', 'run-in-flight', 'dispatched_in_window', 3, 'daily_cap', 4,
        'dry_run', true));

-- 7. The Run in flight: dispatched, no outcome. This is also the Selector's
--    own in-flight lock, which is why the cycle above picked nothing.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '30 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
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
        'area', 'Traveler search'));

-- 8. A cycle that failed outright: the tracker could not be read, so there
--    was no queue to reason about.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '25 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
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
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '20 minutes' + interval '2 seconds', 'issue.skipped',
     jsonb_build_object('cycle', cycle_id, 'number', 663,
        'url', 'https://github.com/' || repo || '/issues/663',
        'reason', 'missing-section',
        'detail', 'no `Acceptance criteria` section')),
    (now() - interval '20 minutes' + interval '3 seconds', 'issue.return-failed',
     jsonb_build_object('cycle', cycle_id, 'number', 663,
        'error', 'GitHub refused the label swap: resource not accessible'));

-- 10. A Proposal whose checks never settled inside the Selector's wait. It is
--     NOT the same card as red checks: nothing is known to be wrong, so the
--     page names no failing check and says the wait ran out.
INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '15 minutes', 'cycle.started',
     jsonb_build_object('repo', repo, 'label', 'ready-for-agent', 'dry_run', false))
    RETURNING id INTO cycle_id;

INSERT INTO journal.events (at, kind, payload) VALUES
    (now() - interval '15 minutes' + interval '2 seconds', 'run.dispatched',
     jsonb_build_object('cycle', cycle_id, 'issue', 658, 'attempt', 1,
        'title', 'Invoices: show the deposit line before the balance',
        'url', 'https://github.com/' || repo || '/issues/658',
        'branch', 'loop/658-deposit-line', 'task_ref', repo || '#658',
        'area', 'Invoice rendering')),
    (now() - interval '10 minutes', 'run.outcome',
     jsonb_build_object('cycle', cycle_id, 'issue', 658, 'attempt', 1,
        'outcome', 'iteration-cap', 'ended_by', 'iteration-cap', 'exit', 0,
        'iterations', 4, 'faults', 'none', 'notified', 'sent',
        'proposal', 'https://github.com/' || repo || '/pull/703')),
    (now() - interval '9 minutes', 'issue.handed-to-human',
     jsonb_build_object('cycle', cycle_id, 'issue', 658, 'attempt', 1,
        'label', 'ready-for-human', 'checks', 'pending',
        'proposal', 'https://github.com/' || repo || '/pull/703'));

END $$;
