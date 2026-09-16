# Evidence-driven engineering

This protocol applies to every model and runtime under `AGENTS.md`. It owns
phase and completion rules; the detailed C++/Qt examples remain in that guide.
Use [change-brief.md](change-brief.md) for a scoped handoff and
[checks.md](checks.md) to select real commands. Do not copy this protocol into
runtime adapters or let an adapter weaken permissions or review coverage.

## 1. Read-only preflight

- Confirm the exact repository, branch, tracked/untracked changes, submodule
  state, applicable instructions, host architecture, toolchain, dependencies,
  and build/output paths. Preserve unrelated changes; never stash, reset, clean,
  install dependencies, terminate processes, or access personal accounts as a
  discovery shortcut. A graph index helps navigation; current source decides.
- Record capabilities as available, absent, unverified, or not needed. An absent
  `../ai-tdesktop` permits directly requested repository work, not a fabricated
  queue id. Use existing queue skills only for authorized queue work.
- Read-only requests permit no builds. Explicit implementation permits bounded
  native Debug validation after preflight unless the request forbids it.
  First configuration/setup requires an agreed command and dependencies;
  never assume `out/` exists. Optimized benchmark/release builds need explicit
  permission. Build permission is not permission to launch against an account.
- Select relevant domain sections, entry points, owners, callers, consumers,
  and test instruments. Tool failure is a capability gap, not a result.

## 2. Acceptance and design

- Before editing, name the objective, non-goals, owned paths, permissions,
  candidate, risks, and observable acceptance criteria. Give each criterion
  an ID and each check an ID, input/state, expected result, negative/control,
  instrument, and falsifier. Keep facts with source locations separate from
  assumptions. A small change needs a small brief, not empty ceremony.
- Trace input -> controller -> session/service -> API/storage -> model -> UI.
  At asynchronous boundaries identify object owner, thread, cancellation, and
  legal state transitions. Compare an existing helper before adding one.
- Independently challenge unsafe assumptions, missing cases, and scope growth.
  Ambiguous ownership, trust, or persistence changes need competent domain
  review. Do not substitute model consensus for source-backed assessment.

## 3. Implementation and ownership

- Assign one integrator as the sole writer of shared briefs, plans, and evidence
  summaries. Independent reviewers return reports without editing source or
  shared artifacts; the integrator records and adjudicates them. This overrides
  `REVIEW.md`'s instruction for reviewers to fix code while reviewing.
- Give implementers exact write sets. Parallelize investigation and review;
  concurrent implementation requires independent interfaces, disjoint paths,
  and separate worktrees and build/output directories. Never overlap writers.
- Where practical, obtain a regression that fails on the baseline for the
  intended reason, then make the smallest coherent correction. Exercise the
  production implementation, not copied logic. Label unavailable baseline
  execution; never synthesize a passing result or substitute a simulation.
- Record changed ownership, cancellation, state, storage, trust, or dependency
  contracts. Material design/scope changes return to assessment. Preserve
  generated ownership and fork integration boundaries; no speculative rewrite.

## 4. Risk-driven review

Every retained change needs independent general review of the complete candidate,
acceptance contract, affected integration, and evidence. Reviewers do not receive
one another's verdicts before forming their own. Clean is a valid outcome.

For queue work retain the existing general reviewer plus all five independent
initial lenses in `../skills/perform-task/references/pipeline.md`; do not retire
lanes via an adapter. Each lens may establish non-applicability from the diff.
The existing no-diff path remains in that protocol. For direct work select
specialist depth using the mechanisms below, with a written applicability
reason. Small size or a "docs-only" label does not retire a relevant risk.

| Lens | Triggers and required questions |
|---|---|
| Lifetime | Ownership, callbacks, threads, RPL, teardown: cancel/guard correctly, account/session replacement, late/duplicate completion, re-entrancy, shutdown? |
| Reuse | New helpers/dependencies/duplicated logic: existing facility, compatible API, genuine need for abstraction? |
| Structure | Multi-stage flows, interfaces, build/generated/persistence boundaries: explicit states, correct owner, consumers and compatibility? |
| Performance | UI work, loops, locks, queues, caches, network/media: bounded work/resources, cancellation, fairness, measurable regression budget? |
| Security | Account data, external input, APIs, updates, files, CI: authorization, isolation, validation, privacy and trust transitions? |

Each finding needs location, trigger, causal mechanism, observable impact,
evidence, confidence, minimal correction direction, and regression oracle.
Separate confirmed defects, measurement hypotheses, and optional improvements.
One integrator resolves disagreements using source/evidence, not voting; rejected
findings need a concrete counterexample or source-backed explanation.
After repairs, re-review and rerun invalidated checks. Carry evidence forward
only with an explicit dependency justification. Two non-converging repair cycles
require reassessment or stronger review, not automatic approval or endless retries.

## 5. C++/Qt and data invariants

Use the specific `AGENTS.md` sections for API lifetime, RPL, serialization,
localization, styling, platform rules, and `REVIEW.md` for mechanical style.

- Prefer existing RAII/Qt ownership. Prove non-owning pointer lifetime;
  `not_null` is not a lifetime guarantee. Guard callbacks or own/cancel requests
  according to delivery semantics; a local guard does not guarantee remote
  delivery, ordering, idempotency, or correct account identity.
- Retain RPL subscription lifetimes, respect move/duplicate conventions, and
  test teardown/re-entrancy. Mutate widgets on the UI thread; bound worker work,
  cancellation, and result handoff without reading live UI/session state there.
- Initialize scalars, distinguish units/identities, validate before narrowing,
  and handle optional/error states explicitly. Respect supported C++20 and
  target Qt APIs; this repository supports differing Qt versions by platform.
- Append sequential storage fields with guarded reads/defaults or use existing
  typed KV preferences. Test actual supported old-format fixtures, restart,
  truncation/corruption, and account isolation where affected; a new-format
  round trip alone does not prove compatibility.
- Keep dimensions in `.style`, strings localized, and reactive values reactive.
  When affected, check theme, scale/DPR, long text, RTL, keyboard, accessibility,
  and native focus/input behavior. Change generated inputs, not generated output.

## 6. Security, privacy, and performance

Map sources, transformations, trust boundaries, and sinks for changed flows.
Check operation-boundary authorization, cross-account caches/callbacks, URL and
redirect policy, path safety, bounded parser/response sizes and timeouts, safe
file staging, and signed update/config validation before use. Review expiry,
rotation, replay/rollback behavior where the protocol defines it.

Never reuse personal Telegram state. A copied test account still holds real
credentials and can send real requests: agree fixtures and action allowlists,
prefer local injection/mocks, and authorize irreversible actions separately.
Never infer an unmarked live folder is disposable because `real_...` exists;
stop and ask for non-destructive preservation. Do not delete either copy.
Account safety and external-action restrictions override older test-loop text.
Redact logs, captures, dumps, private paths, account identifiers, and secrets;
keep sensitive evidence restricted and out of commits, CI uploads, and prompts.

Do not expose production/signing secrets to coding agents or untrusted PR code.
Review dependency pins/advisories and least-privilege CI permissions. Changes
to tests, fixtures, allowlists, thresholds, or gate policy need independent
review; never weaken a change's own gate just to pass. Static/security scanners
support specific claims, not a claim of universal safety. Bound local defensive
fuzzing and malformed-input checks; no external target testing.

Before optimizing, agree workload, metric, regression budget, and baseline.
Compare baseline/candidate on the same native architecture, compiler/Qt,
dataset, scale/DPR, cache and power conditions; separate cold/warm runs.
Retain repeated raw measurements and relevant median/tail/variance, plus
mechanism counts (allocations, queue depth, requests/bytes, cancellation cleanup).
Check fairness and resource bounds, not only average speed. Use native profiling;
Lighthouse is not a Qt gate. Debug timings diagnose; shipped performance needs
an explicitly authorized optimized artifact, otherwise report it unverified.

## 7. Evidence and closure

Choose the narrowest instrument that can falsify each acceptance/risk claim.
Use `test-loop.md` for selected evidence mechanics and read the full harness
README before overlays. Readiness is not a product assertion; negative checks
need a known-present control. Isolate action/log/capture windows. Keep durable
regressions in maintained suites; disposable overlays are not continuous tests.

Record exact command, working directory, environment/toolchain, candidate,
expected/observed results, exit status, full local output/artifact paths, and
unverified coverage. Bind evidence to the actual retained diff, submodules,
build configuration and overlay identity, not an old HEAD label. Respect queue
rules prohibiting commit hashes in AI artifacts; use runner-owned identity
when available. Follow the actual runner's contract; this document does not
specify a receipt schema or claim automated provenance enforcement exists.

Freeze the final candidate and reconcile required checks and independent review.
A missing, failed, stale, skipped, or blocked required check prevents readiness.
Distinguish implementation defect, invalid instrument, and missing capability.
Check assertion counts and outcomes, process exit, crashes/hangs through shutdown;
`TEST_COMPLETE` alone is not a pass. Inventory unintended files and overlays;
state whether the retained uninstrumented candidate was actually exercised.
Report changed paths, verified results, failures, blockers and next valid action.
Ready is not committed, published, merged, or released: each transition needs
its own authorization. Never auto-commit or add attribution trailers.

## 8. Existing capabilities and gaps

`checks.md` lists source-backed script tests, native test targets, and the Debug
harness. Presence is not proof of installation, execution, freshness, or CI
protection. Broad cross-platform PR compile/runtime coverage, native interaction
coverage, sanitizer/fuzz lanes, historical persistence fixtures, performance
baselines, protected evidence provenance, and model-routing evaluations are
provisioning/verification gaps unless demonstrated for the current candidate.

Route models by representative held-out evaluations: correctness, missed defects,
false positives, permission violations, unsupported success claims, recovery,
cost including retries/review, latency, and human intervention. Price, brand,
self-confidence, or repeated agreement is not capability evidence. Escalate
ambiguous/high-risk work to demonstrated stronger review or a human; do not
claim these instructions make a cheaper model equally capable.
