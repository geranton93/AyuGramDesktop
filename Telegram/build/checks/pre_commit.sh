#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/ayugram-pre-commit.XXXXXX")"
keep_logs="${AYUGRAM_PRECOMMIT_KEEP_LOGS:-0}"
cleanup() {
	if [[ "$keep_logs" == "1" ]]; then
		printf 'Diagnostics kept in %s\n' "$temporary_root"
	else
		rm -rf "$temporary_root"
	fi
}
trap cleanup EXIT

staged_files=()
while IFS= read -r path; do
	[[ -n "$path" ]] && staged_files+=("$path")
done < <(git diff --cached --name-only --diff-filter=ACMR)

if ((${#staged_files[@]} == 0)); then
	printf 'No staged files; pre-commit checks are not needed.\n'
	exit 0
fi

failures=0
pass() {
	printf 'PASS %s\n' "$1"
}

skip() {
	printf 'SKIP %s\n' "$1"
}

fail() {
	printf 'FAIL %s\n' "$1"
	failures=$((failures + 1))
}

run_logged() {
	local log_path="$1"
	shift
	"$@" >"$log_path" 2>&1
}

has_unstaged_path() {
	local wanted="$1"
	local path
	while IFS= read -r path; do
		if [[ "$path" == "$wanted" ]]; then
			return 0
		fi
	done < <(git diff --name-only --diff-filter=ACMR -- "$wanted")
	return 1
}

staged_blob() {
	local path="$1"
	local destination="$temporary_root/staged/$path"
	mkdir -p "$(dirname "$destination")"
	git show ":$path" >"$destination"
	printf '%s\n' "$destination"
}

conflict_count=0
while IFS= read -r path; do
	if [[ -n "$path" ]]; then
		conflict_count=$((conflict_count + 1))
	fi
done < <(git diff --cached --name-only --diff-filter=U)
if ((conflict_count == 0)); then
	pass 'no staged merge conflicts'
else
	fail 'staged merge conflicts are unresolved'
fi

if run_logged "$temporary_root/diff-check.log" git diff --cached --check; then
	pass 'staged whitespace and patch checks'
else
	fail 'staged whitespace or patch check failed'
fi

unsafe_path_count=0
for path in "${staged_files[@]}"; do
	case "$path" in
		.env|*/.env|.env.*|*/.env.*)
			case "$path" in
				*.example|*.template) ;;
				*) unsafe_path_count=$((unsafe_path_count + 1)) ;;
			esac
			;;
		*.p12|*.pfx|*.key|*.mobileprovision|*.provisionprofile|*/id_rsa|*/id_ed25519|*private*.pem)
			unsafe_path_count=$((unsafe_path_count + 1))
			;;
		*.pem)
			case "$path" in
				Telegram/Resources/update/root-public.pem|Telegram/Resources/update/rc-config-public.pem) ;;
				*) unsafe_path_count=$((unsafe_path_count + 1)) ;;
			esac
			;;
	esac
done
if ((unsafe_path_count == 0)); then
	pass 'staged filenames contain no private credential artifacts'
else
	fail 'staged filenames contain a private credential artifact'
fi

if command -v rg >/dev/null 2>&1; then
	private_key_pattern='-----BEGIN[[:space:]]+'
	private_key_pattern="${private_key_pattern}(RSA|EC|OPENSSH|DSA|PRIVATE)[[:space:]]+"
	private_key_pattern="${private_key_pattern}PRIVATE KEY-----"
	github_pat_pattern='github'
	github_pat_pattern="${github_pat_pattern}_pat_[[:alnum:]_]{20,}"
	secret_pattern="${private_key_pattern}|gh[pousr]_[[:alnum:]_]{20,}|${github_pat_pattern}|AKIA[0-9A-Z]{16}|xox[baprs]-[[:alnum:]-]{10,}|AIza[[:alnum:]_-]{30,}|sk_(live|test)_[[:alnum:]]{20,}|[0-9]{8,10}:[[:alnum:]_-]{35}"
	api_hash_pattern='TDESKTOP_API_HASH'
	api_hash_pattern="${api_hash_pattern}[^=[:cntrl:]]*=[[:space:]]*"
	api_hash_pattern="${api_hash_pattern}[\"']?[a-fA-F0-9]{32}[\"']?"
	secret_pattern="${secret_pattern}|${api_hash_pattern}"
	if git diff --cached --unified=0 --binary \
		| rg --pcre2 -n "^\\+[^+].*(${secret_pattern})" \
		>"$temporary_root/pattern-scan.log" 2>&1; then
		fail 'staged additions contain a high-confidence secret pattern'
	else
		pass 'staged additions contain no high-confidence secret pattern'
	fi
else
	fail 'ripgrep is required for the staged secret-pattern fallback'
fi

if command -v gitleaks >/dev/null 2>&1; then
	if run_logged "$temporary_root/gitleaks.log" \
		gitleaks git --staged --redact --no-banner --no-color \
		--report-format json --report-path "$temporary_root/gitleaks.json" \
		--exit-code 1 "$repo_root"; then
		pass 'Gitleaks staged secret scan'
	else
		fail 'Gitleaks staged secret scan found a candidate or could not run'
	fi
else
	fail 'Gitleaks is required; install it before committing'
fi

workflow_changed=0
shell_files=()
python_files=()
json_files=()
xml_files=()
dependency_files=()
python_tests_changed=0
version_tests_changed=0
trust_material_changed=0
rc_config_key_changed=0
legacy_release_scripts=()

legacy_release_paths=(
	Telegram/build/build.sh
	Telegram/build/deploy.sh
	Telegram/build/release.sh
	Telegram/build/mac_store_upload.sh
)

for path in "${staged_files[@]}"; do
	case "$path" in
		.github/workflows/*.yml|.github/workflows/*.yaml)
			workflow_changed=1
			;;
		.githooks/*|Telegram/build/checks/*.sh)
			shell_files+=("$path")
			;;
		*.sh)
			case "$path" in
				Telegram/ThirdParty/*) ;;
				*) shell_files+=("$path") ;;
			esac
			;;
		*.py)
			python_files+=("$path")
			case "$path" in
				Telegram/build/generate_update_feed.py|Telegram/build/package_fork_update.py|Telegram/build/tests/test_generate_update_feed.py|Telegram/build/sign_rc_config.py|Telegram/build/tests/test_sign_rc_config.py)
					python_tests_changed=1
					;;
			esac
			;;
		*.json)
			json_files+=("$path")
			;;
		*.xml)
			xml_files+=("$path")
			;;
		Telegram/Resources/update/manifest.sig|Telegram/Resources/update/root-public.pem)
			trust_material_changed=1
			;;
		Telegram/Resources/update/rc-config-public.pem)
			rc_config_key_changed=1
			;;
		Telegram/build/version)
			version_tests_changed=1
			;;
	esac
	case "$path" in
		Telegram/build/version_parser.sh|Telegram/build/tests/test_version_parser.sh)
			version_tests_changed=1
			;;
		Telegram/Resources/update/manifest.min.json)
			trust_material_changed=1
			;;
	esac
	case "$path" in
		package.json|package-lock.json|yarn.lock|pnpm-lock.yaml|poetry.lock|*/poetry.lock|requirements*.txt|*/requirements*.txt|Cargo.lock|*/Cargo.lock|go.mod|*/go.mod|go.sum|*/go.sum|Podfile.lock|*/Podfile.lock|Gemfile.lock|*/Gemfile.lock|conanfile.*|*/conanfile.*|vcpkg.json|*/vcpkg.json)
			dependency_files+=("$path")
			;;
	esac
	for legacy_path in "${legacy_release_paths[@]}"; do
		if [[ "$path" == "$legacy_path" ]]; then
			legacy_release_scripts+=("$path")
		fi
	done
done

if ((${#legacy_release_scripts[@]} > 0)); then
	legacy_failures=0
	for path in "${legacy_release_scripts[@]}"; do
		if git show ":$path" \
			| rg -n '(^|[^[:alnum:]_])eval([[:space:]]|$)|(^|[^[:alnum:]_])set[[:space:]]+\$[A-Za-z_]' \
			>"$temporary_root/legacy-shell-${legacy_failures}.log" 2>&1; then
			legacy_failures=$((legacy_failures + 1))
		fi
	done
	if ((legacy_failures == 0)); then
		pass 'release scripts do not evaluate version metadata as shell code'
	else
		fail 'release scripts contain unsafe shell evaluation or expansion'
	fi
fi

if ((workflow_changed == 1)); then
	if command -v actionlint >/dev/null 2>&1; then
		workflow_failures=0
		for path in "${staged_files[@]}"; do
			case "$path" in
				.github/workflows/*.yml|.github/workflows/*.yaml)
					if ! git show ":$path" \
						| actionlint -no-color -stdin-filename "$path" - \
						>"$temporary_root/actionlint-${workflow_failures}.log" 2>&1; then
						workflow_failures=$((workflow_failures + 1))
					fi
					;;
			esac
		done
		if ((workflow_failures == 0)); then
			pass 'GitHub Actions workflow syntax and embedded shell checks'
		else
			fail 'GitHub Actions workflow checks failed'
		fi
	else
		fail 'actionlint is required when a workflow changes; install it before committing'
	fi
	if command -v zizmor >/dev/null 2>&1; then
		zizmor_failures=0
		for path in "${staged_files[@]}"; do
			case "$path" in
				.github/workflows/*.yml|.github/workflows/*.yaml)
					blob="$(staged_blob "$path")"
					if ! zizmor --pedantic "$blob" \
						>"$temporary_root/zizmor-${zizmor_failures}.log" 2>&1; then
						zizmor_failures=$((zizmor_failures + 1))
					fi
					;;
			esac
		done
		if ((zizmor_failures == 0)); then
			pass 'zizmor GitHub Actions security analysis'
		else
			fail 'zizmor found an issue in a staged workflow'
		fi
	elif [[ "${AYUGRAM_PRECOMMIT_STRICT:-0}" == 1 ]]; then
		fail 'zizmor is required in strict mode when a workflow changes'
	else
		skip 'zizmor is not installed; actionlint remains mandatory'
	fi
fi

if ((${#shell_files[@]} > 0)); then
	if command -v shellcheck >/dev/null 2>&1; then
		shell_failures=0
		for path in "${shell_files[@]}"; do
			dialect='sh'
			if git show ":$path" | head -1 | rg -q 'bash'; then
				dialect=bash
			fi
			shellcheck_args=(-x -s "$dialect")
			case "$path" in
				Telegram/build/build.sh|Telegram/build/deploy.sh|Telegram/build/release.sh|Telegram/build/mac_store_upload.sh)
					shellcheck_args+=(--severity=error)
					;;
			esac
			if ! git show ":$path" \
				| shellcheck "${shellcheck_args[@]}" - \
				>"$temporary_root/shellcheck-${shell_failures}.log" 2>&1; then
				shell_failures=$((shell_failures + 1))
			fi
		done
		if ((shell_failures == 0)); then
			pass 'ShellCheck for staged shell scripts'
		else
			fail 'ShellCheck found an issue in a staged shell script'
		fi
	else
		fail 'ShellCheck is required when a shell script changes; install it before committing'
	fi
fi

if ((${#python_files[@]} > 0)); then
	if command -v python3 >/dev/null 2>&1; then
		python_failures=0
		for path in "${python_files[@]}"; do
			blob="$(staged_blob "$path")"
			if ! python3 -B -c \
				'import sys; compile(sys.stdin.read(), sys.argv[1], "exec")' \
				"$path" < "$blob" \
				>"$temporary_root/python-${python_failures}.log" 2>&1; then
				python_failures=$((python_failures + 1))
			fi
		done
		if ((python_failures == 0)); then
			pass 'Python syntax for staged Python files'
		else
			fail 'Python syntax check failed for a staged Python file'
		fi
	else
		fail 'python3 is required when a Python file changes; install it before committing'
	fi
fi

if ((${#json_files[@]} > 0)); then
	if command -v python3 >/dev/null 2>&1; then
		json_failures=0
		for path in "${json_files[@]}"; do
			blob="$(staged_blob "$path")"
			if ! python3 -c \
				'import json, sys; json.load(sys.stdin)' \
				"$path" < "$blob" \
				>"$temporary_root/json-${json_failures}.log" 2>&1; then
				json_failures=$((json_failures + 1))
			fi
		done
		if ((json_failures == 0)); then
			pass 'JSON syntax for staged JSON files'
		else
			fail 'JSON syntax check failed for a staged JSON file'
		fi
	else
		fail 'python3 is required when a JSON file changes; install it before committing'
	fi
fi

if ((${#xml_files[@]} > 0)); then
	if command -v python3 >/dev/null 2>&1; then
		xml_failures=0
		for path in "${xml_files[@]}"; do
			blob="$(staged_blob "$path")"
			if ! python3 -c \
				'import sys, xml.etree.ElementTree as ET; ET.parse(sys.stdin.buffer)' \
				"$path" < "$blob" \
				>"$temporary_root/xml-${xml_failures}.log" 2>&1; then
				xml_failures=$((xml_failures + 1))
			fi
		done
		if ((xml_failures == 0)); then
			pass 'XML well-formedness for staged XML files'
		else
			fail 'XML well-formedness check failed for a staged XML file'
		fi
	else
		fail 'python3 is required when an XML file changes; install it before committing'
	fi
fi

if ((${#dependency_files[@]} > 0)); then
	if command -v osv-scanner >/dev/null 2>&1; then
		osv_failures=0
		for path in "${dependency_files[@]}"; do
			blob="$(staged_blob "$path")"
			osv_args=(
				osv-scanner scan source
				--lockfile "$blob"
				--format json
				--output-file "$temporary_root/osv-${osv_failures}.json"
				--no-resolve
			)
			if [[ "${AYUGRAM_PRECOMMIT_OSV_OFFLINE:-0}" == 1 ]]; then
				osv_args+=(--offline --offline-vulnerabilities)
			fi
			if ! "${osv_args[@]}" \
				>"$temporary_root/osv-${osv_failures}.log" 2>&1; then
				osv_failures=$((osv_failures + 1))
			fi
		done
		if ((osv_failures == 0)); then
			pass 'OSV dependency scan for staged manifests'
		else
			fail 'OSV dependency scan found a vulnerability or could not complete'
		fi
	else
		fail 'osv-scanner is required when a dependency manifest changes; install it before committing'
	fi
fi

if ((trust_material_changed == 1)); then
	if command -v openssl >/dev/null 2>&1; then
		commit_blob() {
			local path="$1"
			local destination="$temporary_root/commit/$path"
			mkdir -p "$(dirname "$destination")"
			if git diff --cached --name-only --diff-filter=ACMR -- "$path" | rg -q .; then
				git show ":$path" > "$destination"
			else
				git show "HEAD:$path" > "$destination"
			fi
			printf '%s\n' "$destination"
		}
		trust_pub="$(commit_blob Telegram/Resources/update/root-public.pem)"
		trust_manifest="$(commit_blob Telegram/Resources/update/manifest.min.json)"
		trust_signature="$(commit_blob Telegram/Resources/update/manifest.sig)"
		if openssl pkeyutl -verify -pubin -rawin \
			-inkey "$trust_pub" -in "$trust_manifest" -sigfile "$trust_signature" \
			>"$temporary_root/trust-material.log" 2>&1; then
			pass 'signed updater trust material'
		else
			fail 'signed updater trust material verification failed'
		fi
	else
		fail 'openssl is required when updater trust material changes; install it before committing'
	fi
fi

if ((python_tests_changed == 1)); then
	python_test_paths=(
		Telegram/build/generate_update_feed.py
		Telegram/build/package_fork_update.py
		Telegram/build/tests/test_generate_update_feed.py
		Telegram/build/sign_rc_config.py
		Telegram/build/tests/test_sign_rc_config.py
	)
	python_test_blocked=0
	for path in "${python_test_paths[@]}"; do
		if has_unstaged_path "$path"; then
			python_test_blocked=1
		fi
	done
	if ((python_test_blocked == 0)); then
		if run_logged "$temporary_root/update-feed-tests.log" \
			python3 -m unittest \
				Telegram/build/tests/test_generate_update_feed.py \
				Telegram/build/tests/test_sign_rc_config.py -v; then
			pass 'focused updater and remote-config tooling tests'
		else
			fail 'focused updater or remote-config tooling tests failed'
		fi
	else
		fail 'focused updater feed tests require relevant files to have no unstaged edits'
	fi
fi

if ((rc_config_key_changed == 1)); then
	if command -v openssl >/dev/null 2>&1; then
		rc_config_key="$(staged_blob Telegram/Resources/update/rc-config-public.pem)"
		if openssl pkey -pubin -in "$rc_config_key" -text -noout 2>&1 \
			| rg -q 'ED25519' \
			>"$temporary_root/rc-config-key.log" 2>&1; then
			pass 'remote-config trust key is a valid Ed25519 public key'
		else
			fail 'remote-config trust key is not a valid Ed25519 public key'
		fi
	else
		fail 'openssl is required when the remote-config trust key changes; install it before committing'
	fi
fi

if ((version_tests_changed == 1)); then
	version_test_paths=(
		Telegram/build/version
		Telegram/build/version_parser.sh
		Telegram/build/tests/test_version_parser.sh
	)
	version_test_blocked=0
	for path in "${version_test_paths[@]}"; do
		if has_unstaged_path "$path"; then
			version_test_blocked=1
		fi
	done
	if ((version_test_blocked == 0)); then
		if run_logged "$temporary_root/version-tests.log" \
			bash Telegram/build/tests/test_version_parser.sh; then
			pass 'version metadata parser adversarial tests'
		else
			fail 'version metadata parser adversarial tests failed'
		fi
	else
		fail 'version metadata parser tests require relevant files to have no unstaged edits'
	fi
fi

if [[ "${AYUGRAM_PRECOMMIT_FULL:-0}" == 1 ]]; then
	full_gate_failures=0
	full_gate_binaries=(
		out/updater-test-xcode/Debug/test_update_verify
		out/updater-test-xcode/Debug/test_ayu_premium_promo_policy
	)
	for binary in "${full_gate_binaries[@]}"; do
		if [[ ! -x "$binary" ]] || ! run_logged "$temporary_root/$(basename "$binary").log" "$binary"; then
			full_gate_failures=$((full_gate_failures + 1))
		fi
	done
	if ((full_gate_failures == 0)); then
		pass 'optional compiled updater and premium-policy gates'
	else
		fail 'optional compiled updater and premium-policy gates failed or are unavailable'
	fi
else
	skip 'compiled gates disabled; set AYUGRAM_PRECOMMIT_FULL=1 to require them'
fi

if ((failures != 0)); then
	printf '%s pre-commit gate(s) failed.\n' "$failures" >&2
	exit 1
fi

printf 'All staged pre-commit checks passed.\n'
