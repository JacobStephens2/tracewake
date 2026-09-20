# Installing Tracewake

This guide walks an operator from an empty machine to a running instance of
Tracewake and a verified first dry-run cycle. The product teaches one setup:
**Single-Host** (ADR 0019, ADR 0029). One Linux machine is the Host. It runs
the Selector, the PostgreSQL Journal, the dashboard, and the Box locally via
`box-sources/local.sh`. Local dispatch is gated by `loop/assert-credentials.sh`.

A remote Box remains possible as a substituted `SELECTOR_BOX_COMMAND`
(`box-sources/ssh.sh` stays in the tree and in the suite). This guide does not
describe standing that machine up.

---

## 1. System Requirements

One machine, with both the controller's and the Box's needs:

- **OS**: Linux with hardware virtualization support (`/dev/kvm` present)
  for Runs. Ubuntu 22.04 LTS or 24.04 LTS recommended. Rocky Linux 9,
  Debian 12+, or Fedora also work for the controller half; `sbx` itself
  wants Ubuntu 24.04 or later. A local Host on Docker (below) does not
  promise `/dev/kvm`: the dashboard and Selector still run.
- **PostgreSQL**: Version 14 or higher.
- **Python**: Version 3.9 or higher with `python3-venv` and `pip`.
- **Git & GitHub CLI**: `git` 2.30+ and `gh` 2.40+.
- **Virtualization / MicroVM Boundary**: Docker Sandboxes (`sbx`) installed
  and working.
- **Agent CLI**:
  - For Claude: `@anthropic-ai/claude-code` CLI installed and authenticated via a
    subscription token (`CLAUDE_CODE_OAUTH_TOKEN`, ADR 0020).
  - For Codex: `@openai/codex` CLI installed and authenticated (ADR 0025).
- **Network**: Egress access to your forge (e.g. `api.github.com`) and local
  socket or TCP access to PostgreSQL.

---

## 2. Step-by-Step Installation

### Automated Host installation

`deploy/ansible/host.yml` installs the Single-Host components in one play:
PostgreSQL and the Journal schema, the Controller account and checkout, Python
environments, Dashboard and Selector units, the local Box and its Execution
Boundary, and Caddy. The Dashboard listens on `127.0.0.1:8100`; Caddy terminates
TLS for your hostname and forwards the live stream without buffering.

Use an inventory outside this repository, with a `tracewake` group and these
required variables:

| Inventory variable | Supply |
| --- | --- |
| `tracewake_hostname` | The Dashboard's DNS name, without a scheme or path |
| `tracewake_repository` | The product repository's clone URL |
| `tracewake_revision` | The reviewed product revision to install |
| `loop_commit_author_name`, `loop_commit_author_email` | Your commit identity |
| `loop_signing_key_comment` | A label for this Host's signing key |
| `loop_target_repository` | The initial Target's repository slug |
| `loop_scripts_workspace` | Its Box checkout path |

Set `loop_agent_name: grok` in inventory to install Grok and select its egress
allowlist. Omit it to use the product's Claude default. Grok uses the vendor's
shell guest image; `loop_guest_templates` can declare custom image recipes.

```bash
ansible-playbook -i <inventory-path> deploy/ansible/host.yml --check --diff
ansible-playbook -i <inventory-path> deploy/ansible/host.yml
```

Use Ubuntu 24.04 or later with nested virtualization. A first check reports
configuration changes but defers operations requiring a newly installed binary,
account, checkout, or service. Check-mode regression instructions are in
[`deploy/ansible/tests/README.md`](deploy/ansible/tests/README.md).

Point DNS at the Host and allow inbound ports 80 and 443 for Caddy's automatic
certificate issuance. Keep the Dashboard port private. Caddy forwards the
original scheme; the existing sign-in gate protects the public Dashboard.

The play leaves the dispatch timer disabled (`tracewake_dispatch_enabled: false`).
Complete the credentials, Instance files, admin seeding, and first dry-run Cycle
below before enabling it. The Controller account (`conductor`) and Run account
(`loop`) are separate: keep mail secrets with the Controller, and select the
Run account in your Instance's local Box command. Set `LOOP_AGENT_COMMAND` to
the chosen adapter in that Run environment. Inventory installs the agent; it
does not perform its subscription login or supply Instance secrets.

### Local Host (Docker)

The same Single-Host shape, on a laptop (issue #107, ADR 0031). OpenTofu
creates one Ubuntu 24.04 systemd container; `deploy/ansible/host.yml` is
still the play. Nested virtualization is not promised: the dashboard and
Selector run; a Run may fail at the Execution Boundary until `/dev/kvm`
exists.

`wizards/local-host-up.sh` is the laptop walkthrough (issue #114). It
writes instance facts under `~/.config/tracewake/` (or `$TRACEWAKE_CONF`),
runs `deploy/tofu/local/up.sh` and `host.yml`, writes the Instance files
on the container, seeds the first admin, and waits on
`curl http://127.0.0.1:<http_port>/healthz`. `wizards/host-up.sh` stays
the droplet path.

The inventory the wizard writes is the laptop Host: `community.docker.docker`,
`tracewake_tls: false`, `tracewake_manage_checkout: false`,
`tracewake_web_reload: true`. A first apply may stop at `sbx` sign-in
(`wizards/loop-sbx-login.sh`). Caddy is installed before that role, so
`/healthz` should already answer. The cycle timer stays disabled until
credentials exist.

The manual steps below describe those components and the remaining configuration.

### Step 1: Clone Tracewake

```bash
git clone <repository-url> /srv/tracewake
cd /srv/tracewake
```

Ensure your user has read and write permissions in `/srv/tracewake`.

---

### Step 2: Set Up PostgreSQL (The Selector Journal)

The Selector requires a PostgreSQL database to store its append-only Journal.

1. Ensure PostgreSQL is running:
   ```bash
   sudo systemctl enable --now postgresql
   ```

2. Create a database user matching your operating system user (or grant permissions):
   ```bash
   sudo -u postgres createuser --superuser "$USER" 2>/dev/null || true
   ```

3. Create the database and initialize the schema:
   ```bash
   createdb selector
   psql -d selector -f /srv/tracewake/selector/schema.sql
   ```

4. Verify the database tables:
   ```bash
   psql -d selector -c "\dt"
   ```
   *Expected output includes `journal.events` and `selector.control`.*

---

### Step 3: Build Python Virtual Environments

Tracewake isolates the Selector automation and the dashboard in separate virtual
environments.

1. **Build the Selector environment**:
   ```bash
   cd /srv/tracewake/selector
   python3 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -r requirements.txt
   ```

2. **Build the dashboard environment**:
   ```bash
   cd /srv/tracewake/web
   python3 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -r requirements.txt
   ```

---

### Step 4: Prepare Target Workspaces

Tracewake works target repositories without polluting them. You must establish two
clean checkouts of the target repository, both on this Host:

1. **The Work Checkout (`work_repo`)**: Used exclusively by the Selector to run
   `seed-run.sh` and push the initial branch.
2. **The Box Checkout (`box_repo`)**: Used exclusively by the Run script to
   execute the microVM boundary and agent.

*Important: Do not point either path at your active personal editor checkout! A dispatch
switches branches and pushes commits.*

Create the directories:
```bash
# Example for a target named "my-org/my-app"
mkdir -p /srv/workspaces/work /srv/workspaces/box

# Clone the target repository into both paths
git clone git@github.com:my-org/my-app.git /srv/workspaces/work/my-app
git clone git@github.com:my-org/my-app.git /srv/workspaces/box/my-app
```

---

### Step 5: Configure Credentials

Tracewake enforces strict credential isolation (ADR 0009, ADR 0020). Single-Host
dispatch will not start until `loop/assert-credentials.sh` exits 0.

A Host stood up with `wizards/host-up.sh` can finish Google SMTP (readable
only by the Selector account), the agent subscription login, one fine-grained
forge token per Target, and signing-key registration with
`wizards/host-credentials.sh`. The sections below are the generic manual
path; that wizard is the Single-Host walkthrough that also places the mail
secret off the Run account.

#### 1. Host GitHub Authentication
The Selector reads issues and checks rulesets using the `gh` CLI:
```bash
gh auth login
```
Verify authentication:
```bash
gh auth status
```

#### 2. Target Repository Token (`token_file`)
Create the configuration directory first:
```bash
sudo mkdir -p /etc/tracewake/tokens
sudo chown -R "$USER:$USER" /etc/tracewake
```

Create a dedicated GitHub Fine-Grained Personal Access Token scoped strictly to
the target repository (ADR 0009):
- **Repository access**: Only select the target repository (e.g. `my-org/my-app`).
- **Permissions**:
  - `Contents`: Read & write
  - `Pull requests`: Read & write
  - `Issues`: No access (the Box never edits issues; ADR 0010)

Save this token to a restricted file owned by the executing user:
```bash
echo "github_pat_xxx..." > /etc/tracewake/tokens/my-app.token
chmod 0600 /etc/tracewake/tokens/my-app.token
```

#### 3. Model Subscription Credential
Tracewake strictly refuses metered API keys (such as `ANTHROPIC_API_KEY`) to prevent
runaway billing. It requires subscription tokens.

- **For Claude Code**:
  Run the setup token command to mint a 1-year subscription token:
  ```bash
  claude setup-token
  ```
  Export the resulting token into your environment or credentials file:
  ```bash
  export CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-..."
  ```
- **For Codex**:
  Ensure `@openai/codex` is logged in under `~/.codex/config.json`.

#### 4. Execution Boundary Login
Log in to Docker Sandboxes:
```bash
sbx auth login
```
Or run the automated walkthrough:
```bash
/srv/tracewake/wizards/loop-sbx-login.sh
```

#### 5. Verify Credential Inventory
Run the mechanical credential audit:
```bash
cd /srv/tracewake/loop
./assert-credentials.sh
```
*The command must exit 0 with all checks green. Local dispatch uses this same
check as a preflight gate (ADR 0019).*

---

### Step 6: Configure Tracewake

An instance is configured through two files on this Host:
1. `/etc/tracewake/tracewake.env` (Environment variables)
2. `/etc/tracewake/targets.toml` (Target declarations)

Nothing in the product fills these in. A missing required value stops the cycle
at preflight, naming the key.

#### A. Configure `targets.toml`
Create `/etc/tracewake/targets.toml`:

```toml
[[target]]
repo = "my-org/my-app"
work_repo = "/srv/workspaces/work/my-app"
box_repo = "/srv/workspaces/box/my-app"
token_file = "/etc/tracewake/tokens/my-app.token"
guest_template = "loop-base:1"
labeler_allowlist = ["your-github-username"]
review_cap = 20
landing = "propose"

[target.labels]
ready = "ready-for-agent"
needs_info = "needs-info"
review = "awaiting-review"
human = "ready-for-human"
```

#### B. Configure `tracewake.env`

Single-Host Mode (ADR 0019). `SELECTOR_BOX_COMMAND` defaults to
`box-sources/local.sh`; it is written here so the file is the whole instance.

```bash
# /etc/tracewake/tracewake.env
export TRACEWAKE_TARGETS_FILE="/etc/tracewake/targets.toml"
export SELECTOR_JOURNAL_DSN="dbname=selector"

# Single-Host Mode (ADR 0019). The reads are the `-local` siblings, not the
# ssh-based ones: keeping `facts.sh`/`progress.sh` while the host is `local`
# is the `ssh: Could not resolve hostname local` on the box card.
# `SELECTOR_BOX_HOST` is read only by the ssh-based commands and is kept here
# so substituting them back needs no new variable.
#
# The local reads become the Run account through sudo before reading (the
# credential's expiry files live under its home) - beside the conductor-loop
# rule dispatch already needs, each adds one sudoers line (visudo-checked):
#
#   conductor ALL=(loop:loop) NOPASSWD: /srv/tracewake/selector/box-sources/facts-local.sh
#   conductor ALL=(loop:loop) NOPASSWD: /srv/tracewake/selector/box-sources/progress-local.sh *
export SELECTOR_BOX_HOST="local"
export SELECTOR_BOX_COMMAND="box-sources/local.sh"
export SELECTOR_BOX_FACTS_COMMAND="box-sources/facts-local.sh"
export SELECTOR_BOX_PROGRESS_COMMAND="box-sources/progress-local.sh"
export SELECTOR_BOX_LOOP="/srv/tracewake/loop"

# Dashboard URL & Mail Surface
export SELECTOR_LOOP_URL="http://127.0.0.1:8100"
export SELECTOR_NOTIFY_COMMAND="/bin/true"

# Write Protection / Guardrail
export SELECTOR_PROTECTED_REPO="my-org/tracewake-config"
export SELECTOR_PROTECTED_REF="refs/heads/main"
export SELECTOR_PROTECTED_PATHS="guardrail-sources/paths.txt"

# Unenrolled-Target search: the account whose repositories a Cycle
# searches for a Handover label with no Target stanza. Required; no default.
export SELECTOR_SEARCH_OWNER="my-org"
```

---

## 3. Run Your First Dry-Run Cycle

A dry-run cycle exercises the entire configuration, reads the tracker, checks
allowlists and eligibility, queries the Box and Guardrail, and appends the reasoning
to the Journal without modifying git branches or opening pull requests.

1. Source the environment and run the cycle with `--dry-run`:
   ```bash
   cd /srv/tracewake/selector
   set -a && source /etc/tracewake/tracewake.env && set +a
   .venv/bin/python cycle.py --dry-run
   ```

2. **Understand the Output**:
   - **Configuration Validation**: The Selector checks all required variables. If any
     variable is missing, it exits immediately naming the missing key.
   - **Target Processing**: The cycle loads `targets.toml` and evaluates each target in
     turn.
   - **Eligibility Reasoning**: The Selector queries GitHub for issues carrying
     `ready-for-agent`, checks the author against `labeler_allowlist`, evaluates blocker
     dependencies, and prints the evaluated queue.
   - **Box & Guardrail Inspection**: The Selector reads facts from the Box and inspects
     repository protection rules.
   - **Journal Record**: Events `cycle.started` and `cycle.finished` are committed to
     the PostgreSQL database.

3. Inspect the Journal record:
   ```bash
   psql -d selector -c "SELECT id, at, kind, payload FROM journal.events ORDER BY id DESC LIMIT 5;"
   ```

---

## 4. Starting the Dashboard

The dashboard renders the real-time Queue Board and Run history. It requires
sign-in (ADR 0028). There is no registration page: seed the first admin before
the first request.

1. Seed the first admin (fails loudly if that email already exists):
   ```bash
   cd /srv/tracewake/web
   set -a && source /etc/tracewake/tracewake.env && set +a
   WINDOW_ADMIN_PASSWORD='...' .venv/bin/python seed-admin.py you@example.com
   ```

2. Start the web server:
   ```bash
   cd /srv/tracewake/web
   set -a && source /etc/tracewake/tracewake.env && set +a
   .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8100
   ```

3. Open `http://127.0.0.1:8100/` in your browser, sign in, and you will see:
   - The five-column Queue Board (`eligible`, `blocked`, `in-flight`, `awaiting-review`,
     `ready-for-human`).
   - The status strip reporting review capacity and the Guardrail chip.
   - The Run history at `http://127.0.0.1:8100/history`.
   - **Accounts** (admins only): invite a Dashboard Account by email. That send goes through
     `WINDOW_MAIL_COMMAND` (`<to> <subject> [link]`, body on stdin) - the same
     substitutable-command seam as `SELECTOR_NOTIFY_COMMAND`, with the recipient
     named because the dashboard addresses invitees. There is no default.

---

## 5. Automating with Systemd (Unattended Operation)

To run Tracewake continuously without human supervision, install the systemd units
provided in `deploy/systemd/`:

1. Copy the unit files into `/etc/systemd/system/`:
   ```bash
   sudo cp /srv/tracewake/deploy/systemd/tracewake-selector-cycle.service /etc/systemd/system/
   sudo cp /srv/tracewake/deploy/systemd/tracewake-selector-cycle.timer /etc/systemd/system/
   sudo cp /srv/tracewake/deploy/systemd/tracewake-selector-notifier.service /etc/systemd/system/
   sudo cp /srv/tracewake/deploy/systemd/tracewake-web.service /etc/systemd/system/
   ```

2. Reload systemd daemon and enable services:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now tracewake-web.service
   sudo systemctl enable --now tracewake-selector-notifier.service
   sudo systemctl enable --now tracewake-selector-cycle.timer
   ```

3. Verify the timer status:
   ```bash
   systemctl is-active tracewake-selector-cycle.timer
   systemctl list-timers tracewake-selector-cycle.timer
   ```

Tracewake is now fully operational and draining your task queue unattended.

---

## 6. Continuous Deployment

Every push to the default branch (which is how a merged pull request lands)
deploys itself: `.github/workflows/deploy.yml` checks out that revision,
SSHs to the Host as root, and pipes `deploy/cd-update.sh` into a remote
bash. The runner sends the pushed copy, so a Host whose checkout predates
the script still converges. The script then moves `/srv/tracewake` to the
branch tip, refreshes both virtualenvs, re-applies the (idempotent) Journal
schema, and restarts the dashboard and the notifier. The cycle unit is a
oneshot behind its timer, so the next trigger picks the new tree up on its
own. Full provisioning stays in `deploy/ansible/host.yml`; the workflow
only rolls the deployed revision forward. A checkout with local
modifications to tracked files stops the deploy loudly rather than
resetting an operator hotfix away.

The workflow needs one repository variable and two repository secrets
(Settings > Secrets and variables > Actions). The Host's address is
configuration, not code: nothing in the workflow or in `cd-update.sh` names
any particular host.

| Name | Kind | Contents |
| --- | --- | --- |
| `TRACEWAKE_SSH_HOST` | Variable | The Host to deploy: its DNS name or IP |
| `TRACEWAKE_SSH_KEY` | Secret | Private ed25519 key whose public half is in the Host's `/root/.ssh/authorized_keys` |
| `TRACEWAKE_SSH_KNOWN_HOSTS` | Secret | Output of `ssh-keyscan <host>`, verified against the Host's `/etc/ssh/ssh_host_ed25519_key.pub` |

Point the workflow at a Host with (here `<host>` is the Host's DNS name or IP):

```bash
gh variable set TRACEWAKE_SSH_HOST --body "<host>"
ssh-keygen -t ed25519 -C "github-actions-tracewake-deploy" -N "" -f /tmp/tw-deploy-key
ssh root@<host> 'cat >> /root/.ssh/authorized_keys' < /tmp/tw-deploy-key.pub
ssh-keyscan -t ed25519 <host> > /tmp/tw-known-hosts
gh secret set TRACEWAKE_SSH_KEY < /tmp/tw-deploy-key
gh secret set TRACEWAKE_SSH_KNOWN_HOSTS < /tmp/tw-known-hosts
shred -u /tmp/tw-deploy-key /tmp/tw-deploy-key.pub
```

A checkout that is never deployed straight from GitHub (an instance that
advances its serving tree some other way) leaves `TRACEWAKE_SSH_HOST` unset:
the workflow then fails closed naming the variable rather than SSHing
anywhere.
