# Provisioning a local Host

Product OpenTofu (issue #107). It creates one Ubuntu 24.04 systemd
container that can be a Tracewake Host: the local analogue of the droplet
in `deploy/tofu/`.

The container is the machine. `deploy/ansible/host.yml` is still the play
that turns it into a Host (ADR 0031).

## Use

The module takes three required variables and sets no defaults. Keep your
values in a tfvars file on your own machine; they are instance facts and
do not belong in this tree.

```bash
cd deploy/tofu/local
tofu init
tofu plan -var=name=... -var=checkout=... -var=http_port=...
tofu apply -var-file=...
```

Omitting any variable fails before apply. `tofu output container_name`
is the `ansible_host` for `community.docker.docker`. `tofu output http_url`
is where `/healthz` answers after the play has reached the proxy.

A wrapper that keeps state out of the tree:

```bash
deploy/tofu/local/up.sh
```

It reads `$HOME/.config/tracewake/local.tfvars` (or `$TRACEWAKE_CONF/local.tfvars`)
and writes state next to it. The laptop walkthrough that writes those files,
runs this apply, and then `host.yml` is `wizards/local-host-up.sh` (issue #114).

## Shape

Ubuntu 24.04, because that is the image the Execution Boundary supports.
Nested virtualization is not promised on a laptop: the window and Selector
still run; a Run may fail at `sbx` until `/dev/kvm` exists.

The checkout is bind-mounted at `/srv/tracewake`. Named volumes hide the
in-tree `.venv` directories so a Linux virtualenv does not collide with
the operator's, and keep `/var/lib/postgresql` and `/etc/tracewake` so
recreating the container does not drop the Journal or the instance files.
Inventory should set `tracewake_manage_checkout: false` so an apply does
not reset the working tree.
