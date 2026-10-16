# Shared engineering workflow

This document applies to Codex and Claude Code in this checkout. Read it before
non-trivial engineering work. It supplements `AGENTS.md`, `REVIEW.md`, and the
applicable workflow skills; it does not replace their task lifecycle, build,
publication, or account-safety rules. Explicit user instructions take priority.

## Establish the scope and evidence

- Inspect the current checkout, branch, dirty files, applicable instructions,
  and available tools. Preserve unrelated work. Do not assume a remembered
  branch, installed app, graph, dependency version, or prior fix is current.
- Identify the user-visible behavior, affected consumers, invariants, and
  acceptance criteria. For an investigation, distinguish candidates from
  confirmed defects. Do not start implementation from a request to investigate.
- State security and performance implications during planning, implementation,
  and review. A justified not-applicable result is acceptable; silence is not.
- Use this loop: map behavior, form a falsifiable hypothesis, reproduce or
  measure, make a bounded change, verify the consumer path, refresh knowledge.
  Do not repeat broad checks after a fix unless it invalidates their evidence.

## Source discovery and graph freshness

For structural code questions, use this order:

1. CodeGraph explore when `.codegraph/` exists: symbols, callers, ownership
   candidates, imports, and blast radius.
2. AgentMemory recall, when available, for previous decisions and context.
3. Graphify query, when a relevant graph exists, for architecture and non-code
   relationships.
4. Exact text/configuration searches with `rg`; use focused source inspection
   when graph coverage is absent or demonstrably incorrect.

Before the first graph-backed analysis, after source edits, and after switching
branches or updating submodules, run `codegraph sync` from the exact checkout
and inspect its exit status, then `codegraph status`. Coordinate with an active
writer rather than starting overlapping rebuilds. Before final conclusions,
repeat synchronization if indexed inputs changed. Missing tools, permissions,
locks, or sync errors mean graph freshness is unverified: report the limitation
and use direct source inspection, without treating stale edges as evidence.
Do not automatically install tools, delete locks, or rebuild the entire index.

- An asynchronous hook or daemon reduces lag; it does not replace this barrier.
  Never suppress synchronization failures in a required verification step.
- Freshness includes relevant dirty/untracked source, deleted/renamed files,
  submodules, parser version, and indexing configuration, not just Git HEAD.
  Recheck inputs if another process edits them during analysis.
- Confirm important graph relationships against actual declarations and call
  sites. C++ templates, macros, overloads, generated code, callbacks, and virtual
  dispatch can produce missing or incorrect edges. Missing graph test links do
  not prove missing tests; a test link does not prove behavioral coverage.
- Keep worktrees distinct. Exclude build outputs, nested worktrees, account
  data, logs, and generated noise from knowledge extraction unless explicitly
  needed. Document meaningful coverage exclusions.
- If index drift persists, diagnose scope and parser coverage before proposing
  a controlled full reconciliation. Never claim that a status label proves
  complete semantic accuracy.

## Explain how the application works

Trace a concrete journey through entry point, state owner, requests/storage,
worker execution, presentation, cancellation, and destruction. Identify thread
affinity, callback ownership, cache bounds, retry policy, and persistence rules.
Read adjacent consumers before changing a shared helper.

Architecture claims need source paths/symbols and an evidence status: observed,
inferred, or unverified. Runtime claims also need the executed scenario and
result. A reference edge is not an ownership edge. An old decision is context,
not proof of the current implementation.

## Find lifetime, resource, and concurrency defects

Exercise transitions: open, load, cancel, seek, switch, close, reopen, reconnect,
and session teardown. Inspect partial initialization and error paths as well.

- Trace every shorter-lived capture in session-owned API callbacks. Apply the
  `AGENTS.md` rules for `crl::guard`, cancellation, and owned `MTP::Sender`;
  preserve requests that must reach the server.
- Check QObject parents, thread affinity, context-bound connections, deferred
  destruction, queued callbacks, re-entrancy, and `rpl::lifetime` scope.
- Require bounds and eviction/teardown for caches, static/session maps, queues,
  and retained subscriptions. RAII does not bound a live container.
- Trace native resources separately: QRhi/GPU objects, CoreVideo buffers,
  FFmpeg contexts, file descriptors, and platform handles, including failures.
- Use compatible ASan/UBSan, leak detection, and separate TSan configurations
  when runtime evidence requires them and builds are authorized. Record actual
  toolchain/platform support and instrumentation scope; do not assume it.
- For growth, repeat lifecycle cycles after warm-up and cleanup settling;
  measure surviving allocations, object counts, cache bytes, and native/GPU
  resources. RSS alone cannot distinguish leaks from allocator retention.
  Reachable unbounded storage can escape conventional leak detection.

A finding records location, trigger, violated invariant, causal path, impact,
confidence, reproduction state, remediation, and regression evidence. For a
security finding, include exploitability and trust boundaries. Do not present
speculative smells or old audit findings as confirmed vulnerabilities.

## Review simplicity and reuse

Use the existing lifetime, reuse, structure, performance, and security lenses
from the performer workflow. Follow its independent review/delegation contract
when executing it; this document alone does not require agents for every edit.

- DRY: find duplicated policy that can diverge; check existing helpers first.
  Similar syntax alone does not justify merging different responsibilities.
- KISS/YAGNI: reduce unnecessary states, indirection, and coordination. Require
  real consumers before adding generalized frameworks or abstraction layers.
- Responsibility and encapsulation: keep UI, protocol, persistence, and policy
  boundaries explicit; make invalid states difficult to represent.
- Use complexity, duplication, churn, and clang-tidy/Qt-aware diagnostics to
  select inspection targets, not as automatic defect or rewrite verdicts.
  Verify compile commands and generated headers match the actual build context.
- Prefer bounded changes to maintained code over repository-wide cleanup.
  Account for upstream merge cost and prove behavior preservation.

## Measure responsiveness and media performance

Start with an observable journey: chat opening, image presentation, video
startup/seeking, media-heavy scrolling, or closing media. Trace the critical
path through queueing, cache/disk, network, decoding, pixel conversion, texture
upload, and painting. These stages can overlap; use a timeline.

- Measure first usable image/frame, seek latency, frame/event-loop stalls,
  request and byte counts, retries, cancellation waste, memory, and CPU.
- Compare repeated baseline and candidate runs with the same device, build
  configuration, fixture, codec/size, cache state, network conditions, and
  concurrent workload. Record sample count and variability; use percentiles
  only with enough observations to support them.
- Profile the actual bottleneck before changing concurrency, chunk sizes,
  caching, prefetch, or worker placement. Check existing deduplication and
  priorities before proposing another mechanism.
- Keep GUI work on the main thread; honor QObject affinity. Bound worker
  queues, reject obsolete results, and preserve cancellation and backpressure.
- Require bandwidth, memory, battery/CPU, fairness, integrity, and protocol
  guardrails. Faster startup does not justify unbounded prefetch or bypassing
  verification. Include slow/offline/error and rapid-switch cases.
- Debug/sanitizer runs establish relevant correctness evidence, not production
  speed. Do not build Release contrary to repository policy; report the limit
  if representative performance evidence needs separately authorized tooling.

## Verify with the appropriate instrument

Read `Telegram/build/quality/README.md` for the prerequisite doctor, scoped
static analyzers, isolated Debug presets, sanitizer support matrix, and library
activation gates. Use these commands instead of inventing an analysis toolchain
or modifying the existing build tree. Unsupported and unprepared lanes remain
explicitly unverified; never claim that every sanitizer ran.

Read `Telegram/SourceFiles/test/README.md` before designing app scenarios. In
the task workflow, follow its pipeline and `.agents/shared/test-loop.md`.

Use focused logic/component checks for isolated rules, real application paths
for integration/lifetime behavior, and visual captures for rendering claims.
Mocks or standalone reproductions prove only the mechanisms they exercise.
Choose the cheapest instrument that proves the required property, and escalate
when it cannot. Documentation-only changes need link/diff checks, not an app
build. Never weaken a necessary runtime check because builds are unavailable.

Tests need a discriminating assertion, a known control where useful, and hostile
cases such as late responses, teardown during loading, repeated opening,
corrupt input, and cold caches. Follow harness rules for readiness versus
product assertions and exact object identity. Keep instrumentation in `test/`
or the disposable overlay, as required by `AGENTS.md`.

Retain reusable regression coverage or its replayable scenario through the
existing workflow. Report passed, failed, not applicable, and not run distinctly.
Tie evidence to the actual candidate, fixture, and toolchain; honor task artifact
rules forbidding commit hashes where applicable. State unverified behavior.

## Maintain knowledge and propose improvements

At completion, inspect whether changed behavior invalidates maintained
architecture documentation, graph relationships, or scenario expectations.
Update affected maintained documentation within scope and synchronize CodeGraph.
For an existing Graphify graph, use its applicable skill/update procedure and
revalidate semantic claims; code extraction alone does not validate prose.
Do not silently create a second whole-repository graph or force destructive
graph replacement. If an update is unavailable, report what remains stale.

Knowledge records should link claims to relevant sources/fingerprints and
evidence, mark inference explicitly, and retire invalidated claims. Store
ephemeral plans/review receipts in the external task workspace or temporary
storage, not the source tree. Do not persist chat contents, credentials, or
account data in graphs. Persistent personal/agent memory writes require the
user's explicit authorization; recall does not grant write permission.

Discover features from demonstrated user friction, failure recovery, repeated
manual work, or measured bottlenecks. Each proposal needs the user problem,
evidence, smallest useful change, alternatives, acceptance criteria, and
security/resource tradeoffs. Compare expected benefit with implementation and
upstream maintenance cost; do not invent speedup percentages.

Route new work through the documented `ai-tdesktop` inbox and skills. If that
repository is absent, report the missing path and preserve the proposal in the
response; do not invent a queue, claim work, or mutate unrelated task state.

## Loading and enforcement limits

Instructions express the shared operating contract; they are not a runtime or
CI enforcement mechanism. On each host verify that the required files were
read and the selected tools are callable. After context loss, reload required
instructions rather than assuming they survived. Never claim cross-assistant
enforcement from a pointer file, configured hook, or another assistant's run.
Mandatory machine gates require separately implemented and tested automation.
