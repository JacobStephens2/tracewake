# The Host, as a local machine: one Ubuntu 24.04 systemd container and
# nothing else (issue #107).
#
# Single-Host (ADR 0029, ADR 0031) runs the Selector, the Journal, the
# Dashboard and the Box on this one machine. Nested virtualization is
# not promised: Docker Desktop often has no /dev/kvm, so a Run may fail
# at the Execution Boundary while the window and Selector still run.
#
# The image is the local analogue of ubuntu-24-04-x64. Ansible's host.yml
# is what turns the empty machine into a Host; this module does not.

resource "docker_image" "host" {
  name = "tracewake-host-machine:24.04"

  build {
    context    = "${path.module}/machine"
    dockerfile = "Dockerfile"
  }

  keep_locally = true
}

# Linux virtualenvs overlay the bind-mounted tree so they do not collide
# with the operator's own .venv directories on the laptop.
resource "docker_volume" "selector_venv" {
  name = "${var.name}-selector-venv"
}

resource "docker_volume" "web_venv" {
  name = "${var.name}-web-venv"
}

# Recreating the container must not drop the Journal or the instance
# files (issue #114). Same pattern as the Linux venvs above.
resource "docker_volume" "postgresql" {
  name = "${var.name}-postgresql"
}

resource "docker_volume" "tracewake_etc" {
  name = "${var.name}-etc"
}

resource "docker_container" "host" {
  name  = var.name
  image = docker_image.host.image_id

  hostname   = var.name
  privileged = true
  restart    = "unless-stopped"
  tty        = true

  # systemd is PID 1. Docker's own init would steal that.
  cgroupns_mode = "host"

  mounts {
    type   = "bind"
    source = var.checkout
    target = "/srv/tracewake"
  }

  mounts {
    type   = "volume"
    source = docker_volume.selector_venv.name
    target = "/srv/tracewake/selector/.venv"
  }

  mounts {
    type   = "volume"
    source = docker_volume.web_venv.name
    target = "/srv/tracewake/web/.venv"
  }

  mounts {
    type   = "volume"
    source = docker_volume.postgresql.name
    target = "/var/lib/postgresql"
  }

  mounts {
    type   = "volume"
    source = docker_volume.tracewake_etc.name
    target = "/etc/tracewake"
  }

  mounts {
    type   = "tmpfs"
    target = "/run"
  }

  mounts {
    type   = "tmpfs"
    target = "/tmp"
  }

  ports {
    internal = 80
    external = var.http_port
  }
}

output "container_name" {
  description = "Docker name ansible_host uses with community.docker.docker."
  value       = docker_container.host.name
}

output "http_url" {
  description = "Where the Dashboard answers after host.yml has reached the proxy."
  value       = "http://127.0.0.1:${var.http_port}"
}
