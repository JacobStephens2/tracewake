# Product OpenTofu: one Host for Single-Host Tracewake (issue #55).
#
# The five identifiers that make this somebody's machine - name, region,
# size, VPC and SSH key - are required variables with no product defaults.
# Omitting any of them fails before apply, which is the point: instance
# facts belong in the operator's tfvars on his machine, not in the tree.

variable "name" {
  type        = string
  description = "Droplet name. The operator's own hostname for the Host."
}

variable "region" {
  type        = string
  description = "Cloud region slug. Must offer nested virtualization."
}

variable "size" {
  type        = string
  description = "Droplet size slug. Must offer nested virtualization."
}

variable "vpc_uuid" {
  type        = string
  description = "UUID of the VPC the Host joins."
}

variable "ssh_key" {
  type        = string
  description = "SSH key ID or fingerprint the Host trusts at first boot."
}
