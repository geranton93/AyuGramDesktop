# Check command registry

These commands are source-backed options, not an execution report. No command
is claimed exercised by this document's adoption. Select checks by acceptance
and risk under [engineering.md](engineering.md); record actual commands, cwd,
environment, candidate, exit status and full output in the change evidence.
A command's presence never grants permission or proves installed prerequisites.

All paths below are relative to the repository root unless stated otherwise.
Timeouts below are initial execution caps, not measured runtimes; record an
explicit budget before running and report a timeout as incomplete evidence.
Use a runner with a wall-clock cap rather than assuming a `timeout` utility
exists on macOS. Do not install tools merely to make a registry entry runnable.

## Read-only preflight and source checks

| ID | Command | Prerequisites / side effects | Cap / expected output |
|---|---|---|---|
| repo-state | `git status --short` | Git checkout; no writes | 30s; literal tracked/untracked state, not a cleanliness assumption |
| submodule-state | `git submodule status --recursive` | Git checkout; does not initialize/update modules | 60s; module identities and state prefixes; inspect dirty module diffs separately |
| diff-whitespace | `git diff --check` and `git diff --cached --check` | Git checkout; no writes; excludes untracked content | 30s each; exit 0 with no errors checks whitespace only |
| toolchain-macos | `uname -m`, `xcodebuild -version`, `cmake --version` | Run separately on macOS when relevant; no setup/install | 30s each; actual architecture/tool versions or explicit absence |

Read existing non-secret CMake cache and generated output metadata for the
chosen tree; do not dump credential-bearing configuration. Confirm generator,
source directory, architecture, Debug configuration, dependencies and targets.
`Telegram/build/qt_version.py` declares Qt 6.11.2 on macOS and Windows ARM/qt6,
and Qt 5.15.19 on other Windows paths; this is not installed-library evidence.
`AGENTS.md` explains first configure versus preserving cached Qt on macOS.

## Focused script and trust checks

Run from the repository root. Inspect the selected test's dependencies and
fixtures first. Python may create `__pycache__`; tests may use temporary files.
Use `PYTHONDONTWRITEBYTECODE=1` when bytecode writes are outside the allowed scope.
These checks do not build or launch the app and do not authorize publishing.

| ID | Exact command | Source / prerequisites | Cap / output contract |
|---|---|---|---|
| update-feed | `python3 -m unittest Telegram/build/tests/test_generate_update_feed.py -v` | `.github/workflows/updater-ci.yml`; Python 3 and test imports/tools | 120s; named cases and unittest success, exit 0; retain failures |
| remote-config | `python3 -m unittest Telegram/build/tests/test_sign_rc_config.py -v` | Same workflow; Python 3 and selected test's crypto tooling | 120s; named cases and unittest success, exit 0 |
| version-parser | `bash Telegram/build/tests/test_version_parser.sh` | `Telegram/build/checks/pre_commit.sh`; Bash and test prerequisites | 120s; shell assertions succeed, exit 0 |
| update-root-signature | `openssl pkeyutl -verify -pubin -rawin -inkey Telegram/Resources/update/root-public.pem -in Telegram/Resources/update/manifest.min.json -sigfile Telegram/Resources/update/manifest.sig` | Updater CI; OpenSSL supporting the committed signature algorithm, public files only | 30s; verification success and exit 0; no private keys needed |

The updater workflow also checks committed feed shape, old endpoints, and the
remote-config public key type. Read its exact checks when those surfaces change.
The pre-commit script is a staged-snapshot gate, not a universal working-tree
suite: it conditionally runs tests and refuses relevant unstaged files. Do not
stage or commit just to run it. Its optional `AYUGRAM_PRECOMMIT_FULL=1` gate
runs fixed-path binaries; their presence alone does not prove freshness.
Hook installation, remote CI runs and branch protection require separate proof.

## Native Debug compile and execution (conditional)

No builds for read-only work. Explicit implementation permits bounded native
Debug builds after preflight unless the user forbids them. Configuration,
dependency setup, account use and cleanup need their own agreed scope. In an
unconfigured checkout record the missing tree; do not execute these templates.

| ID | Command template | Prerequisites / side effects | Cap / output contract |
|---|---|---|---|
| debug-app | `cmake --build "<verified-build-root>" --config Debug --target Telegram --parallel <agreed-jobs>` | Compatible configured tree/toolchain/dependencies; writes build outputs and may regenerate or compile dependencies | Agreed finite build budget; exit 0 plus resolved artifact identity; not a runtime pass |
| debug-component | `cmake --build "<verified-build-root>" --config Debug --target <verified-test-target> --parallel <agreed-jobs>` | Same, with target enabled; writes build outputs | Agreed finite build budget; successful compile/link is not test execution |
| component-run | `"<resolved-test-executable>"` | Inspect test entry point/fixtures; verify final candidate and architecture; record required cwd | 120s initial cap for a bounded headless test; assertion results and clean exit, no crash/hang |

Replace placeholders only from inspected configuration and generated target
metadata. Do not assume `out/Debug/Telegram.exe`, a macOS bundle name, or a
universal `ctest` suite. `AGENTS.md` retains host-specific build guidance.

Source-backed targets:

- `Telegram/CMakeLists.txt` enables `cmake/tests.cmake` with
  `DESKTOP_APP_TEST_APPS`: `test_data_unsent_read_generation`,
  `test_data_unsent_read_till`, and `test_ayu_ghost_mode_peer_exceptions` are
  headless candidates; inspect their entry points for the selected check.
- The same file defines `test_text` with GUI dependencies; do not treat it as
  an interchangeable unattended headless check.
- `test_update_verify` and `test_ayu_premium_promo_policy` are included when
  `DESKTOP_APP_TEST_APPS` or `DESKTOP_APP_SPECIAL_TARGET` is enabled; inspect
  `Telegram/cmake/test_update_verify.cmake` and
  `Telegram/cmake/test_ayu_premium_promo_policy.cmake` before selecting them.
- Dependencies on these targets compile them; they do not execute the tests.

## Runtime evidence and unprovisioned gates

The existing Debug harness lives in `Telegram/SourceFiles/test/README.md`;
`test_scenario.cpp` is a disposable no-op overlay slot in the retained source.
For a selected app check read that README and `test-loop.md`, verify the actual
workspace helper contract, and record account/action permissions first. Never
run account setup to discover whether personal data is safe. Do not infer that
an unmarked live folder is disposable from coexistence with a preserved folder.
The helper may control processes and account files: this registry deliberately
does not offer it as a permission-free smoke command.

Require the selected assertions/check count, isolated logs/captures, exit status,
and crash/hang evidence through shutdown, not merely `TEST_COMPLETE`. Record
overlay and binary identity and distinguish tested instrumentation from retained
uninstrumented code. UI/native input claims need the appropriate direct oracle.

Broad cross-platform native PR builds/runtime gates, native interaction suites,
sanitizers/fuzzing, historical persistence fixture coverage, performance
baselines, and protected candidate/evidence provenance are gaps until provisioned
and verified. Existing updater CI and release workflows do not establish those
capabilities. Optimized benchmarks/releases require explicit permission; no
Debug timing or successful macOS run proves shipped or cross-platform behavior.
