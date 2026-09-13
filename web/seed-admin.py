#!/usr/bin/env python3
"""Create the first window admin. There is no registration page.

    WINDOW_ADMIN_PASSWORD=... python seed-admin.py operator@example.com

Fails loudly if that email is already an account. The password is read from
WINDOW_ADMIN_PASSWORD, or from the terminal if that is unset.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB.parent / "selector"))

import auth  # noqa: E402


def _password() -> str:
    env = os.environ.get("WINDOW_ADMIN_PASSWORD")
    if env:
        return env
    if sys.stdin.isatty():
        return getpass.getpass("Password: ")
    return sys.stdin.readline().rstrip("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the first window admin. There is no registration page."
    )
    parser.add_argument("email", help="the admin's email")
    args = parser.parse_args(argv)
    password = _password()
    if not password:
        sys.stderr.write("seed-admin.py: password is empty\n")
        return 2
    try:
        auth.create_admin(args.email, password)
    except auth.AccountExists as exc:
        sys.stderr.write(
            f"seed-admin.py: account {exc.email} already exists\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
