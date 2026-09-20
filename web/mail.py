"""The dashboard's outbound mail seam (issue #41).

One substitutable command configured on the Instance, mirroring the Selector's
notification surface (ADR 0018): the provider lives entirely in configuration.
The dashboard additionally names the recipient, because it addresses invitees
rather than a fixed operator inbox.

    $WINDOW_MAIL_COMMAND <to> <subject> [link]      # body on stdin

There is no default. How an instance sends mail - which relay, whose
credentials, from whom - is the instance's fact and not the product's, so an
unset `WINDOW_MAIL_COMMAND` is refused by name rather than filled in.
"""
from __future__ import annotations

import os
import subprocess

import targets


class MailFailed(Exception):
    """The mail command could not run, or refused the message."""


def command() -> str:
    """The configured mail command, or a named refusal if it is unset."""
    return os.environ.get("WINDOW_MAIL_COMMAND") or targets.missing(
        "WINDOW_MAIL_COMMAND", "the dashboard's mail surface"
    )


def send(*, to: str, subject: str, link: str, body: str) -> None:
    """Hand one message to the mail command. Raises on anything but success."""
    timeout = int(os.environ.get("WINDOW_MAIL_TIMEOUT_SECONDS") or 120)
    try:
        completed = subprocess.run(
            [command(), to, subject, link],
            input=body,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MailFailed(f"the mail command could not run: {exc}") from exc
    if completed.returncode != 0:
        said = (completed.stderr or completed.stdout or "").strip()
        raise MailFailed(
            f"the mail command refused (exit {completed.returncode}): "
            f"{said[:400]}"
        )
