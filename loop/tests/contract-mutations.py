"""The deliberate breaks tests/mutation-check.sh applies to contract.sh.

contract.sh executes nothing: it declares the Termination Contract's values and
renders the summary that is written into the Progress Log at Run start. That
summary is the only account of a Run's terms that outlives the Run - the prompt
is a scratch file the Loop deletes on its way out - so a line missing from it is
a Run whose record cannot say what it was working under, silently and for good.

Its own file rather than an entry in tests/mutations.py because mutation-check
applies mutations to ONE script at a time and restores it afterwards; a break
declared here is applied to contract.sh, and tests/loop.bats is the suite that
has to notice, exactly as it does for run.sh.
"""

import pathlib
import sys

MUTATIONS = {
    # The discipline the Iterations were told to work in stops being recorded,
    # so a reviewer reading the Progress Log afterwards cannot tell whether the
    # Run was asked for tests-first work and a review before each commit (#162).
    "summary-drops-the-skills": (
        "- Discipline skills: ${LOOP_DISCIPLINE_SKILLS}\n",
        "",
    ),
    # The names stop being one value the prompt and the summary share, which is
    # how a Run comes to be prompted for one discipline and to record another.
    "skills-are-two-values": (
        ': "${LOOP_DISCIPLINE_SKILLS:=',
        ': "${LOOP_DISCIPLINE_SKILLS:=work however you like}"\n'
        ': "${LOOP_DISCIPLINE_SKILLS_UNREAD:=',
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
        print(
            f"contract-mutations.py: {name} no longer applies to {target}",
            file=sys.stderr,
        )
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
