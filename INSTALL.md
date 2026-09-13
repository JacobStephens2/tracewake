# Installing Tracewake

This guide walks an operator from an empty machine to a running instance of
Tracewake and a verified first dry-run cycle. It covers both supported architectural
shapes:

1. **Two-Machine Shape**: A **Controller** host running the Selector, PostgreSQL Journal,
   and Web window, connected over SSH to a dedicated **Box** host that runs the
   microVM Execution Boundary and agent processes.
2. **Single-Machine Shape**: A single Linux machine or developer workstation running both
   the Controller and the Box locally via `box-sources/local.sh` (ADR 0019).

---

## 1. System Requirements

### Controller Requirements
- **OS**: Linux (Rocky Linux 9, Ubuntu 22.04+, Debian 12+, or Fedora).
- **PostgreSQL**: Version 14 or higher.
- **Python**: Version 3.9 or higher with `python3-venv` and `pip`.
- **Git & GitHub CLI**: `git` 2.30+ and `gh` 2.40+.
- **Network**: Egress access to your forge (e.g. `api.github.com`) and local socket or
  TCP access to PostgreSQL.

### Box Requirements (Two-Machine or Single-Machine)
- **OS**: Linux with hardware virtualization support (`/dev/kvm` present). Ubuntu 22.04
  LTS or 24.04 LTS recommended.
- **Virtualization / MicroVM Boundary**: Docker Sandboxes (`sbx`) installed and working.
- **Agent CLI**:
  - For Claude: `@anthropic-ai/claude-code` CLI installed and authenticated via a
    subscription token (`CLAUDE_CODE_OAUTH_TOKEN`, ADR 0020).
  - For Codex: `@openai/codex` CLI installed and authenticated (ADR 0025).
- **SSH Access** (*Two-machine shape only*): Key-based SSH access from the Controller user
  to an unprivileged `loop` user on the Box host.

---

## 2. Choosing Your Architectural Shape

| Decision Factor | Two-Machine Shape | Single-Machine Shape |
| --- | --- | --- |
| **Use Case** | Production unattended automation, team infrastructure, always-on servers. | Developer laptops, single-operator experimentation, fast local evaluation. |
| **Boundary Isolation** | Strong physical isolation: Controller (holding tracker tokens and Journal) and Box (running arbitrary untrusted agent code) are separate VMs. | Logical isolation: MicroVM boundary (`sbx`) runs on the same machine, gated by `assert-credentials.sh`. |
| **Box Driver** | `SELECTOR_BOX_COMMAND="box-sources/ssh.sh"` | `SELECTOR_BOX_COMMAND="box-sources/local.sh"` |
| **Box Host Setting** | `SELECTOR_BOX_HOST="loop.example.com"` (or hostname/IP) | `SELECTOR_BOX_HOST="local"` (required by preflight validator). |

---

## 3. Step-by-Step Installation

### Step 1: Clone Tracewake
Clone the Tracewake repository onto your Controller machine:

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

Tracewake isolates the Selector automation and the Web window in separate virtual
environments.

1. **Build the Selector environment**:
   ```bash
   cd /srv/tracewake/selector
   python3 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -r requirements.txt
   ```

2. **Build the Web window environment**:
   ```bash
   cd /srv/tracewake/web
   python3 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -r requirements.txt
   ```

---

### Step 4: Prepare Target Workspaces

Tracewake works target repositories without polluting them. You must establish two
clean checkouts of the target repository:

1. **The Work Checkout (`work_repo`)**: Located on the Controller. Used exclusively by
   the Selector to run `seed-run.sh` and push the initial branch.
2. **The Box Checkout (`box_repo`)**: Located on the Box (or locally in single-machine
   mode). Used exclusively by the Run script to execute the microVM boundary and agent.

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

Tracewake enforces strict credential isolation (ADR 0009, ADR 0020).

#### 1. Controller GitHub Authentication
The Controller reads issues and checks rulesets using the `gh` CLI:
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

On the Box (or local machine), create a dedicated GitHub Fine-Grained Personal Access
Token scoped strictly to the target repository (ADR 0009):
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
*The command must exit 0 with all checks green.*

---

### Step 6: Configure Tracewake

An instance is configured through two files:
1. `/etc/tracewake/tracewake.env` (Environment variables)
2. `/etc/tracewake/targets.toml` (Target declarations)

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

##### Option 1: Single-Machine Shape (Local Execution)
```bash
# /etc/tracewake/tracewake.env
export TRACEWAKE_TARGETS_FILE="/etc/tracewake/targets.toml"
export SELECTOR_JOURNAL_DSN="dbname=selector"

# Single-Host Mode (ADR 0019)
export SELECTOR_BOX_HOST="local"
export SELECTOR_BOX_COMMAND="box-sources/local.sh"
export SELECTOR_BOX_FACTS_COMMAND="box-sources/facts.sh"
export SELECTOR_BOX_PROGRESS_COMMAND="box-sources/progress.sh"
export SELECTOR_BOX_LOOP="/srv/tracewake/loop"

# Web Window URL & Mail Surface
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

##### Option 2: Two-Machine Shape (Remote Box via SSH)
```bash
# /etc/tracewake/tracewake.env
export TRACEWAKE_TARGETS_FILE="/etc/tracewake/targets.toml"
export SELECTOR_JOURNAL_DSN="dbname=selector"

# Remote Box over SSH
export SELECTOR_BOX_HOST="loop.example.com"
export SELECTOR_BOX_USER="loop"
export SELECTOR_BOX_COMMAND="box-sources/ssh.sh"
export SELECTOR_BOX_FACTS_COMMAND="box-sources/facts.sh"
export SELECTOR_BOX_PROGRESS_COMMAND="box-sources/progress.sh"
export SELECTOR_BOX_LOOP="/home/loop/loop"

# Web Window URL & Mail Surface
export SELECTOR_LOOP_URL="https://tracewake.example.com"
export SELECTOR_NOTIFY_COMMAND="/srv/tracewake/scripts/send-email.sh"

# Write Protection / Guardrail
export SELECTOR_PROTECTED_REPO="my-org/tracewake-config"
export SELECTOR_PROTECTED_REF="refs/heads/main"
export SELECTOR_PROTECTED_PATHS="guardrail-sources/paths.txt"

# Unenrolled-Target search: the account whose repositories a Cycle
# searches for a Handover label with no Target stanza. Required; no default.
export SELECTOR_SEARCH_OWNER="my-org"
```

---

## 4. Run Your First Dry-Run Cycle

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

## 5. Starting the Web Window

The Web window renders the real-time Queue Board and Run history. It requires
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
   - **Accounts** (admins only): invite a Window Account by email. That send goes through
     `WINDOW_MAIL_COMMAND` (`<to> <subject> [link]`, body on stdin) - the same
     substitutable-command seam as `SELECTOR_NOTIFY_COMMAND`, with the recipient
     named because the window addresses invitees. There is no default.

---

## 6. Automating with Systemd (Unattended Operation)

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
