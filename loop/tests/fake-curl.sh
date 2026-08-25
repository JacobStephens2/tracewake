#!/usr/bin/env bash
#
# The scripted fake `curl`, for the pull-request surface's offline suite (#83).
#
# pr-sources/github.sh calls `curl` by name, which is the seam: putting this on
# PATH ahead of the real one lets the suite assert what the script would send to
# GitHub - the method, the path, and the JSON body, including `draft: true` -
# without a token, a network, or a pull request opened on a real repository.
#
#   FAKE_CURL_STATE      a scratch path. Each call appends "<method> <url>" to
#                        "<path>.calls" and writes the --data payload to
#                        "<path>.payload".
#   FAKE_CURL_BEHAVIOUR  none | existing | refuse-post | refuse-get

set -euo pipefail

state="${FAKE_CURL_STATE:?FAKE_CURL_STATE must be set}"

method=GET
url=""
data=""
while (($# > 0)); do
    case "$1" in
        --request) method="$2"; shift 2 ;;
        --data) data="$2"; shift 2 ;;
        --header | -H) shift 2 ;;
        --silent | --show-error | --fail-with-body) shift ;;
        *) url="$1"; shift ;;
    esac
done

printf '%s %s\n' "${method}" "${url}" >>"${state}.calls"
[[ -n ${data} ]] && printf '%s' "${data}" >"${state}.payload"

case "${method}:${FAKE_CURL_BEHAVIOUR:-none}" in
    GET:existing) printf '[{"html_url": "https://github.com/owner/name/pull/17"}]\n' ;;
    GET:refuse-get)
        printf '{"message": "Bad credentials"}\n'
        exit 22
        ;;
    GET:*) printf '[]\n' ;;
    POST:refuse-post)
        printf '{"message": "Validation Failed", "errors": [{"message": "No commits between master and loop/run-648"}]}\n'
        exit 22
        ;;
    POST:*) printf '{"html_url": "https://github.com/owner/name/pull/999", "draft": true}\n' ;;
esac
