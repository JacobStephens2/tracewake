# The box, as a cloud resource - Educational Travel Adventures' own stanza,
# with the account-specific identifiers left as placeholders.
#
# A separate host from the controller, deliberately. The controller holds the
# Journal, the operator's identity and (at ETA) production database grants; an
# unattended agent must not be one compromise away from any of that, which is
# what ADR 0003 and ADR 0009 argue. An operator with one machine runs the box
# on the controller instead, and the credential inventory is the gate on that.
#
# Two things about the image are load-bearing rather than taste:
#
#   Ubuntu 24.04, not the fleet's Rocky. `sbx` (Docker Sandboxes), the
#   hypervisor the Execution Boundary is built from, supports Ubuntu 24.04 or
#   later and no Rocky release.
#
#   Nested virtualization. `sbx` boots a microVM per Iteration, so a size or
#   region that does not offer it gives a box on which no Run can start.
#
# Backups are off because the box holds no unique state worth restoring: its
# output is a pushed branch and a Proposal, its configuration is ansible, and
# its credentials are re-issuable. A rebuild is a re-provision.

resource "digitalocean_droplet" "tracewake_box" {
  name     = "loop.etadventures.com"
  region   = "nyc1"
  size     = "s-2vcpu-4gb"
  image    = "ubuntu-24-04-x64"
  vpc_uuid = "<your VPC uuid>"

  # The controller's key, pinned by id rather than looked up by title: a
  # lookup by title breaks silently the day somebody renames the key.
  ssh_keys = ["<your ssh key id>"]

  backups       = false
  monitoring    = true
  droplet_agent = true
  ipv6          = false

  tags = ["tracewake", "box"]

  lifecycle {
    prevent_destroy = true
    # The provider does not return these after creation.
    ignore_changes = [ssh_keys, user_data]
  }
}
