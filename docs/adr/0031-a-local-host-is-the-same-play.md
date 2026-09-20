# A local Host is the same play, on a Docker Ubuntu machine

Issue #107. Developing Tracewake by SSHing to the production Host made the
inner loop a deploy. The Host is already declared in two places: OpenTofu
creates the machine, Ansible configures it. A laptop should stand up that
same Single-Host shape without a droplet.

## Decision

OpenTofu's job stays the machine. `deploy/tofu/` remains the DigitalOcean
droplet (issue #55). `deploy/tofu/local/` is a sibling root module that
creates one Docker container from an Ubuntu 24.04 systemd image. The
Dockerfile is the local analogue of `ubuntu-24-04-x64`: systemd and enough
Python for the configuration play to connect. It does not install Tracewake,
Caddy, PostgreSQL, or the Execution Boundary.

Ansible's job stays the Host. The same `host.yml` play configures the
container. There is no second play and no compose stack that re-implements
the roles. Instance facts stay out of the tree, in `~/.config/tracewake/`,
as they already do for the droplet.

Three inventory flags make a laptop Host usable without changing production
defaults:

- `tracewake_tls: false` — Caddy listens on `:80` with `auto_https off`. A
  local Host has no public DNS name, so ACME cannot issue. Production keeps
  automatic HTTPS on `tracewake_hostname`.
- `tracewake_manage_checkout: false` — the play leaves `/srv/tracewake`
  alone. The local module bind-mounts the working tree there, and an apply
  must not reset the operator's branch to `tracewake_revision`. Named Docker
  volumes hide the in-tree `.venv` directories so a Linux virtualenv does
  not collide with the operator's.
- `tracewake_web_reload: true` — the window unit passes uvicorn `--reload`,
  so a save in the bind-mounted tree is the inner loop.

`tracewake_proxy` runs before `loop_execution_boundary`. Caddy is what makes
the window reachable from outside the Host (the unit binds loopback). A
missing Docker sign-in fails the boundary role; the Dashboard must already
answer.

The Docker apt source follows the Host's architecture (`amd64` or `arm64`).
Hardcoding `amd64` is what the droplet needed and what an arm64 local Host
cannot use. The `kvm` group is declared before `loop` joins it: the stock
Ubuntu container image does not ship one, and the cloud image does.

## Nested virtualization is not promised

`sbx` boots a microVM through `/dev/kvm`. Docker Desktop on a laptop often
has no such device. The window, the Journal, and the Selector still run. A
Run may fail at the Execution Boundary until the machine can boot a
microVM. That is a Host whose Box cannot yet run, not a second topology.

## Consequences

INSTALL documents the local Host as Single-Host on Docker. It is not a
"dev instance" (that name is Attended Preview's to avoid, ADR 0016) and not
a two-machine setup. `wizards/host-up.sh` stays the droplet path.

The configuration test pins the local module the same way it pins the
droplet: required variables, no product defaults, one Ubuntu 24.04 machine.
