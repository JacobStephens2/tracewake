"""The Deploy workflow must start CD even when the Host checkout
predates the Host-side script (issue #105).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def test_deploy_workflow_supplies_the_cd_script_so_a_behind_script_host_can_start():
    """The Host-side copy arrives via the checkout. A Host whose revision
    predates the script fails with exit 127 if the workflow bashes that
    path. The runner must send the pushed copy instead.
    """
    workflow = WORKFLOW.read_text()
    assert "actions/checkout" in workflow
    assert "bash -s" in workflow
    assert "< deploy/cd-update.sh" in workflow
    assert "bash /srv/tracewake/deploy/cd-update.sh" not in workflow


def test_deploy_workflow_fails_closed_when_the_host_is_unset():
    """A checkout that is never deployed from GitHub leaves the host
    variable unset. The workflow names that variable and does not SSH.
    """
    workflow = WORKFLOW.read_text()
    assert "TRACEWAKE_SSH_HOST is not set" in workflow
    host_check_at = workflow.index('HOST="${{ vars.TRACEWAKE_SSH_HOST }}"')
    ssh_at = workflow.index("ssh -i ~/.ssh/tracewake_deploy")
    unset_at = workflow.index("TRACEWAKE_SSH_HOST is not set")
    assert host_check_at < unset_at < ssh_at
