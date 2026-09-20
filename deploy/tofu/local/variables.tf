# Local Host machine: one Docker container (issue #107).
#
# The three identifiers that make this somebody's machine - name, the
# checkout to bind-mount, and the host port Caddy is published on - are
# required variables with no product defaults. Omitting any of them fails
# before apply. Instance facts belong in the operator's tfvars, not in
# this tree.

variable "name" {
  type        = string
  description = "Container name. The operator's own hostname for the local Host."
}

variable "checkout" {
  type        = string
  description = "Absolute path of the product checkout bind-mounted at /srv/tracewake."
}

variable "http_port" {
  type        = number
  description = "Host port published to the container's HTTP reverse proxy (port 80)."
}
