#!/usr/bin/env bash
# shellcheck disable=SC2154
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../version_parser.sh
# shellcheck disable=SC1091,SC2154
source "$script_dir/../version_parser.sh"

temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/ayugram-version-test.XXXXXX")"
cleanup() {
	rm -rf "$temporary_root"
}
trap cleanup EXIT

valid_version=$'AppVersion 7002006\nAppVersionStrMajor 7.2\nAppVersionStrSmall 7.2.6\nAppVersionStr 7.2.6\nBetaChannel 1\nAlphaVersion 0\nAppVersionOriginal 7.2.6.beta\n'
printf '%s' "$valid_version" > "$temporary_root/valid"
read_version_file "$temporary_root/valid"
# shellcheck disable=SC2154
[[ "$AppVersion" == 7002006 ]]
[[ "$AppVersionStr" == 7.2.6 ]]
[[ "$BetaChannel" == 1 ]]

assert_rejected() {
	local name="$1"
	local contents="$2"
	local path="$temporary_root/$name"
	printf '%s' "$contents" > "$path"
	if read_version_file "$path"; then
		printf 'Expected %s to be rejected.\n' "$name" >&2
		exit 1
	fi
}

assert_rejected 'command-substitution' $'AppVersion 7002006$(touch /tmp/ayugram-version-test-marker)\n'
assert_rejected 'unknown-key' $'Unexpected 1\n'
assert_rejected 'duplicate-key' $'AppVersion 7002006\nAppVersion 7002007\n'
assert_rejected 'extra-field' $'AppVersion 7002006 trailing\n'
assert_rejected 'invalid-channel' $'BetaChannel 2\n'

printf 'Version parser tests passed.\n'
