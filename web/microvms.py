"""The Box's currently running microVMs, as `sbx ls` reports them.

The dashboard calls `sample()`. Tests replace it. A failure raises SamplerError;
the dashboard degrades the widget and still draws the board.

This is a request-time read, like Host telemetry, not a Journal replay: a
microVM lives for one Iteration and is gone when the Iteration ends, so a
Cycle-time observation would almost always be empty. The box-surface command
is substitutable (ADR 0004); the product default is the local read.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
DEFAULT_COMMAND = str(HERE / "selector" / "box-sources" / "microvms-local.sh")
DEFAULT_TIMEOUT_SECONDS = 10


class SamplerError(Exception):
    """The box's microVMs could not be listed."""


@dataclass(frozen=True)
class MicroVM:
    name: str
    agent: str
    status: str
    workspace: str


@dataclass(frozen=True)
class Sample:
    vms: tuple[MicroVM, ...]


def sample() -> Sample:
    """The microVMs the box is holding right now."""
    command = os.environ.get("SELECTOR_BOX_MICROVMS_COMMAND", DEFAULT_COMMAND)
    try:
        timeout = int(
            os.environ.get(
                "SELECTOR_BOX_MICROVMS_TIMEOUT_SECONDS",
                str(DEFAULT_TIMEOUT_SECONDS),
            )
        )
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    try:
        done = subprocess.run(
            [command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SamplerError(
            f"the box did not list its microVMs within {timeout}s"
        ) from exc
    except OSError as exc:
        raise SamplerError(f"{command}: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise SamplerError(
            detail[-1] if detail else f"{command}: exit {done.returncode}"
        )
    try:
        vms = _parse(done.stdout)
    except ValueError as exc:
        raise SamplerError(str(exc)) from exc
    return Sample(vms=vms)


def _parse(stdout: str) -> tuple[MicroVM, ...]:
    """`sbx ls --json` into records. Unknown shapes are a sampler failure,
    not a guessed list: an empty object and a list of unnamed rows would
    both render as 'none running' and hide a format change.

    The CLI's JSON uses a `sandboxes` array; that key is the vendor's, and
    it stays inside this parser. What the dashboard sees is microVMs.
    """
    text = stdout.strip()
    if not text:
        return ()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("the box's microVM list was not JSON") from exc
    if not isinstance(payload, dict) or "sandboxes" not in payload:
        raise ValueError("the box's microVM list was not in the expected shape")
    items = payload["sandboxes"]
    if not isinstance(items, list):
        raise ValueError("the box's microVM list was not in the expected shape")
    vms: list[MicroVM] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("a microVM entry was not an object")
        name = _text(item.get("name"))
        if not name:
            raise ValueError("a microVM entry had no name")
        vms.append(
            MicroVM(
                name=name,
                agent=_text(item.get("agent")) or "not reported",
                status=_text(item.get("status")) or "not reported",
                workspace=_workspace(item.get("workspace")) or "not reported",
            )
        )
    return tuple(vms)


def _text(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _workspace(value: object) -> str:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return _text(value.get("path")) or _text(value.get("name"))
    return ""
