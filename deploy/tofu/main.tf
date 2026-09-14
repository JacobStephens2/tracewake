# The Host, as a cloud resource: one droplet and nothing else (issue #55).
#
# Single-Host (ADR 0029) runs the Selector, the Journal, the Dashboard and
# the Box on this one machine, so there is no second stanza for a separate
# box. Local dispatch is gated by the credential inventory instead (ADR
# 0019).
#
# Two things about the image are load-bearing rather than taste:
#
#   Ubuntu 24.04, not anything else on the fleet. `sbx` (Docker Sandboxes),
#   the hypervisor the Execution Boundary is built from, supports Ubuntu
#   24.04 or later and nothing older.
#
#   Nested virtualization. `sbx` boots a microVM per Iteration, so a size or
#   region that does not offer it gives a Host on which no Run can start.
#   The size and region variables must name such a shape; the module does
#   not second-guess the cloud's current list.
#
# Backups are off because the Host holds no unique state worth restoring:
# its output is a pushed branch and a Proposal, its configuration is
# ansible, and its credentials are re-issuable. A rebuild is a
# re-provision.

resource "digitalocean_droplet" "host" {
  name     = var.name
  region   = var.region
  size     = var.size
  image    = "ubuntu-24-04-x64"
  vpc_uuid = var.vpc_uuid

  # The operator's key, pinned by id or fingerprint rather than looked up by
  # title: a lookup by title breaks silently the day somebody renames it.
  ssh_keys = [var.ssh_key]

  backups       = false
  monitoring    = true
  droplet_agent = true
  ipv6          = false

  tags = ["tracewake", "host"]

  lifecycle {
    prevent_destroy = true
    # The provider does not return the keys after creation.
    ignore_changes = [ssh_keys]
  }
}

output "host_ip" {
  description = "The Host's public IPv4 address, for the DNS wizard."
  value       = digitalocean_droplet.host.ipv4_address
}
