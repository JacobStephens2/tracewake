# Provisioning the Host

Product OpenTofu (issue #55). It creates one Ubuntu 24.04 droplet that can
be a Tracewake Host: the image the Execution Boundary supports, on a shape
with nested virtualization.

## Use

The module takes five required variables and sets no defaults. Keep your
values in a tfvars file on your own machine; they are instance facts and do
not belong in this tree.

```bash
cd deploy/tofu
tofu init
tofu plan -var=name=... -var=region=... -var=size=... -var=vpc_uuid=... -var=ssh_key=...
tofu apply -var-file=...
```

Omitting any variable fails before apply. `tofu output host_ip` gives the
address the DNS wizard points the hostname at.

## Shape

The size and region must offer nested virtualization: `sbx` boots a
microVM per Iteration, so anything without it is a Host on which no Run can
start. Backups stay off; a rebuild is a re-provision.
