"""The deliberate breaks tests/mutation-check.sh applies to the notification surface.

One entry per thing that makes a notification honest: that it goes to the
proposal and not to something else, that it needs the token, that a refusal is a
failure rather than a silence, and that the body it sends is the Run's.
"""

import pathlib
import sys

MUTATIONS = {
    # Any URL shaped roughly like a GitHub one is accepted, so a comment the box
    # has no permission to make is attempted against whatever it was handed -
    # an issue thread, or a host that is not GitHub.
    "pull-request-url-unvalidated": (
        r"[[ ${pr_url} =~ ^https://github\.com/"
        r"([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/([0-9]+)$ ]] ||",
        r"[[ ${pr_url} =~ ^https?://[^/]+/"
        r"([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/[a-z]+/([0-9]+)$ ]] ||",
    ),
    # The token stops being required, so a box that has none still makes the
    # request and learns what it holds from GitHub's refusal.
    "token-not-required": (
        '[[ -s ${token_file} ]] ||\n    die "no GitHub token at ${token_file}'
        " - the box cannot tell anybody the Run finished\"",
        "[[ -s ${token_file} ]] || true",
    ),
    # GitHub refusing the comment is reported as a notification that was sent,
    # which is the one lie this surface must not tell: the operator is told
    # nothing and the Run says he was told.
    "refusal-reported-as-sent": (
        '    die "GitHub refused the comment: ${answer:-no response}"',
        '    answer=\'{"html_url": "https://github.com/owner/name/pull/999"}\'',
    ),
    # The subject stops leading the comment, so the outcome is no longer the
    # first line a notification list shows.
    "subject-dropped": (
        "    printf '%s\\n\\n' \"${subject}\"\n",
        "",
    ),
    # A body file that is not there becomes an empty comment rather than a
    # refusal, so the operator is notified of nothing at all.
    "empty-body-sent": (
        '[[ -f ${body_file} ]] || die "no such body file: ${body_file}"',
        '[[ -f ${body_file} ]] || : >"${body_file}"',
    ),
}


def main() -> int:
    if sys.argv[1] == "--list":
        print("\n".join(MUTATIONS))
        return 0
    name, target = sys.argv[1], pathlib.Path(sys.argv[2])
    old, new = MUTATIONS[name]
    source = target.read_text()
    if old not in source:
        print(f"notify-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
