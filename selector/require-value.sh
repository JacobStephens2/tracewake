# The refusal every Selector command shares. Sourced, never executed.
#
#   source "<selector>/require-value.sh"
#   require SELECTOR_BOX_HOST
#
# An instance value has exactly one right answer per instance and no right
# answer in the product (issue #3), so the commands that read one refuse when
# it is absent rather than filling it in. What is here is the SENTENCE they
# refuse with, in one place: four scripts spelling out the same message is
# four chances for one of them to drift into saying something else about the
# same rule, which is the seam `contract.sh` exists to close on the Loop's
# side.
#
# `${!name}` is bash's indirect expansion, which is why the callers pass a
# NAME rather than a value: a helper handed the value could not report which
# variable was missing, and the variable's name is the whole of the useful
# part of the message.
#
# It calls `die` when the caller has one - every Selector command does, and
# each prefixes its own name - and falls back to a plain message so that a new
# command sourcing this before it has written one still refuses legibly.

require() {
    local name="$1"
    [[ -n ${!name:-} ]] && return 0
    local message="${name} is not set, and there is no default for it: it is a fact about this instance, not about Tracewake."
    if declare -F die >/dev/null 2>&1; then
        die "${message}"
    fi
    printf '%s\n' "${message}" >&2
    exit 1
}
