# Local analogue of DigitalOcean's ubuntu-24-04-x64 image.
# systemd is PID 1 so the Host units have a manager; python3 is what the
# configuration play connects with. Packages the play installs — the
# window proxy, the Journal, the Execution Boundary — stay out of this
# image, or a laptop Host and a droplet Host would drift.
FROM ubuntu:24.04

ENV container=docker
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        systemd \
        systemd-sysv \
        dbus \
        python3 \
        python3-apt \
        sudo \
        ca-certificates \
    && apt-get clean \
    && systemctl mask \
        systemd-udevd.service \
        systemd-udevd-kernel.socket \
        systemd-udevd-control.socket \
        getty@tty1.service \
        console-getty.service \
        systemd-firstboot.service

STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd"]
