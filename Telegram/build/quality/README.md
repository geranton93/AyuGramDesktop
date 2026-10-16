# Analysis and sanitizer operations

Read `AGENTS.md` and `AGENT_WORKFLOW.md` first. These tools prepare isolated
analysis; they do not grant permission to build, launch an account, or publish.
The default application configuration is unchanged. Never reconfigure the
existing `out/` tree with these profiles.

## Prerequisites and static analysis

Run from the repository root:

```sh
python3 -B Telegram/build/quality/quality.py doctor
python3 -B Telegram/build/quality/test_quality.py
cmake --list-presets
```

The doctor returns 2 when a tool or compilation database is missing. It does
not install anything or build the application. `LLVM_BIN` selects a toolchain
directory; macOS also discovers Homebrew LLVM without changing the global PATH.
Keep compiler, clang-tidy, Clazy and their runtime/library versions compatible.
Record exact versions in each analysis receipt. On macOS the preparation used
LLVM 23.1.0, Clazy 1.17.1 and cppcheck 2.21.0; refresh that inventory on other
hosts rather than assuming those versions are installed.

For a new macOS analysis tree, pass the Qt version actually installed beside
this checkout and the matching compiler paths. This example uses the locally
verified Qt 6.11.2 and Homebrew LLVM. The preset unsets exported `QT` so a later
reconfigure cannot silently overwrite `qt_requested`.

```sh
cmake --preset quality-static -Dqt_requested=6.11.2 -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_C_COMPILER=/opt/homebrew/opt/llvm/bin/clang \
  -DCMAKE_CXX_COMPILER=/opt/homebrew/opt/llvm/bin/clang++ \
  -DCMAKE_OBJC_COMPILER=/opt/homebrew/opt/llvm/bin/clang \
  -DCMAKE_OBJCXX_COMPILER=/opt/homebrew/opt/llvm/bin/clang++
```

Use `x86_64` on an Intel Mac. Ninja plus Swift requires a single architecture;
do not inherit the normal universal Xcode architecture list.
Configuration can run compiler probes but does not establish that Telegram
builds. Generated headers must be produced by the appropriate authorized build
before analysis; do not fabricate includes or reuse stale generated outputs.
The presets select Debug, Ninja, test API credentials and two build jobs.
They do not supply an account. Review host dependency/options requirements
before configuration; Linux dependencies differ from macOS.

After the compilation database and generated headers are valid, select actual
translation units (not headers). Repeat `--file` to expand scope deliberately:

```sh
python3 -B Telegram/build/quality/quality.py analyze --tool clang-tidy \
  --file Telegram/SourceFiles/media/streaming/media_streaming_loader_mtproto.cpp
python3 -B Telegram/build/quality/quality.py analyze --tool clazy-standalone \
  --file Telegram/SourceFiles/media/streaming/media_streaming_loader_mtproto.cpp
python3 -B Telegram/build/quality/quality.py analyze --tool cppcheck \
  --file Telegram/SourceFiles/media/streaming/media_streaming_loader_mtproto.cpp
```

Use `--database /absolute/path/compile_commands.json` for another tree. Paths in
`--file` are relative to the invoking directory. The runner filters the original
database without rewriting compiler flags or executing its compile commands.
Tools parse the selected files and their includes. Missing tools, missing TUs,
parse errors and diagnostics must not become a clean result. Clang-tidy and
Clazy warnings fail the run; triage existing findings separately from introduced
ones rather than adding blanket suppressions. Save stdout/stderr outside the
source tree. Analysis can consume substantial CPU: start with affected TUs.

Clang's path-sensitive static analyzer is enabled through `clang-analyzer-*` in
`.clang-tidy`. `scan-build` is available for a separately authorized instrumented
build campaign; do not run both over the whole repository on every edit.
Cppcheck is an independent analysis engine; Clazy adds Qt-specific checks.
Complexity diagnostics select review candidates and are not proof of a defect.

## Sanitizer lanes

Select exactly one `AYUGRAM_SANITIZER` per clean tree. The available presets are
`quality-asan-ubsan`, `quality-ubsan`, `quality-tsan`, and `quality-lsan`.
These instrumented presets currently accept macOS only. The all-other GLib GIR
scanner performs a nested link that currently handles only the legacy ASan
option; propagating and verifying each runtime there is a prerequisite to
enabling these profiles on those hosts. They fail explicitly until supported.
The static-analysis preset is not subject to this sanitizer host restriction.
Use the same host arguments as the static preset. A compiler/link probe rejects
unsupported runtimes. A successful probe is not full application compatibility.
Flags apply to source targets created below the root; prebuilt dependencies are
not retroactively instrumented. Flags cover C/C++/Objective-C/Objective-C++;
Swift code is not instrumented by these profiles. Keep matching flags at
compile and final link.

| Instrument | Use and constraints |
| --- | --- |
| ASan + UBSan | Default memory-safety/undefined-behavior lane; frame pointers and fatal UBSan diagnostics. Detects only executed instrumented paths. |
| UBSan alone | Lower-overhead focused undefined behavior; does not replace ASan. Optional integer/conversion checks require triage of intentional wraparound. |
| TSan | Separate race lane. Never combine with ASan or LSan. Requires supported OS/runtime; prebuilt libraries leave coverage gaps. |
| LSan | Separate leak lane when supported, or leak detection in ASan when its runtime supports it. Detects unreachable allocations, not all retained growth. On macOS use Instruments when runtime support is absent. |
| MSan | Uninitialized reads. Preparation-only here: requires a supported host and an instrumented dependency closure, including compatible C++ runtime, Qt and codecs. The prebuilt macOS stack is not eligible. |
| HWASan | Preparation-only: supported hardware/OS/runtime such as Linux AArch64; not a generic macOS ASan substitute. Use a dedicated supported-host toolchain. |
| CFI | Preparation-only: requires compatible Clang/LTO, visibility and dependency boundaries. Do not add flags to this mixed prebuilt stack and claim whole-program coverage. |
| libFuzzer + ASan/UBSan | Dedicated bounded parser/component harnesses with corpus, timeout, memory limit, and retained crash cases. Do not add `-fsanitize=fuzzer` to Telegram's existing main. |
| SanitizerCoverage | Coverage feedback/instrumentation, not a defect detector or a substitute for assertions. |
| Native diagnostics | Instruments Allocations/Leaks/Time Profiler and GPU tooling on macOS; supported heap/race profilers on other hosts. Needed for native resources and reachable growth. |

Native Windows uses its supported compiler configuration: the existing
`DESKTOP_APP_ASAN` option is separate from these Unix-driver profiles. Do not
combine the options. MSVC ASan is not MSan/TSan/UBSan support. Treat the project's
all-other platform branch correctly, but verify individual tool support on the
actual OS rather than assuming every Unix host supports every sanitizer.

When a build is explicitly authorized:

```sh
cmake --build --preset quality-asan-ubsan
```

Run the built executable only with the isolated fixture/account and lifecycle
scenario selected by the existing test workflow. Recommended fatal diagnostic
settings include `UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1` and
`TSAN_OPTIONS=halt_on_error=1`. For ASan, verify whether leak detection is
supported before using `ASAN_OPTIONS=detect_leaks=1`. Keep a matching symbolizer
available; retain the original stack and exit status. No global suppressions.
Any narrow suppression needs a source, rationale, owner and recheck condition.

## Dependency preparation and activation

The OpenSSL preparation pin was updated from 3.2.1 to 3.5.8 LTS, with its
resolved commit pinned in `Telegram/build/prepare/prepare.py`. The official
release site lists 3.5 support through April 2030; 3.2 is out of support.
This is a source recipe update, not a rebuilt or installed library.

Before activating it, use isolated dependency storage, run OpenSSL's own tests
in a compatible test-enabled configuration, and validate the project's static
library layout on Windows and macOS. Verify both macOS slices, headers versus
archives, Debug/Release Windows library/PDB paths, application linking, TLS and
crypto consumer behavior. Preserve the prior dependency tree for rollback.
The existing preparation recipe uses `no-tests`; it is not test evidence.

Qt 6.11.2 and FFmpeg 6.1.6 were already selected in the inspected recipes. No
blanket submodule or major-version update is required merely to run analyzers.
For further upgrades inventory the resolved pin, advisories, actual reachable
API, upstream patches, ABI/build compatibility, license changes and required
regressions. A version match alone does not establish vulnerability exposure.
Do not update submodules to arbitrary branch tips. Record deferred compatibility
checks rather than describing recipe changes as deployed remediation.

## Routine flow

1. Read shared instructions; inspect dirty state and refresh the code graph.
2. Select a bounded change and baseline reproduction/measurement.
3. Run applicable static analyzers over affected TUs and inspect consumers.
4. Select sanitizer/runtime/visual checks based on changed mechanisms.
5. Review lifetime, reuse, structure, performance and security; resolve findings.
6. Retain discriminating regression coverage; refresh affected knowledge.
7. Report exact tools, scope, passed/failed/unrun checks and remaining limits.

No hosted CI is installed by these local presets. A future CI lane must supply
the pinned compiler/dependencies, generated headers and safe test fixtures;
do not publish an empty green job that only lists tools. The Claude hook is a
supplement to the shared synchronous graph barrier, not cross-agent enforcement.

## Primary references

- [LLVM sanitizer documentation](https://clang.llvm.org/docs/)
- [MemorySanitizer dependency requirements](https://clang.llvm.org/docs/MemorySanitizer.html)
- [HWASan supported platforms](https://clang.llvm.org/docs/HardwareAssistedAddressSanitizer.html)
- [Clazy operation and compiler compatibility](https://github.com/KDE/clazy)
- [OpenSSL releases and support](https://openssl-library.org/source/)
- [OpenSSL 3.5 release notes](https://openssl-library.org/news/openssl-3.5-notes/)
