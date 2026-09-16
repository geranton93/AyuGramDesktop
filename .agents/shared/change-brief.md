# Change brief — <objective>

Template for [engineering.md](engineering.md). The integrator owns this shared
artifact; reviewers return independent read-only reports. Keep it proportional
to the change. Queue work retains its existing required artifacts and statuses;
map these fields into them rather than creating a competing queue record.
This is a human-readable brief, not a machine receipt schema or proof of a run.

## Scope and ownership

- Repository / branch / host / architecture:
- Objective and observable outcome:
- Non-goals / forbidden scope expansion:
- Integrator / shared artifact writer:
- Implementer and exact owned write paths:
- Read-only paths / other writers' changes to preserve:
- Reviewer scopes and report destinations:
- Queue context: existing authorized id, or direct work without a queue id:

## Permissions and preflight

- Allowed edits / commands / build configuration / resource and time limits:
- Explicit prohibitions and permission source:
- Setup, dependency installation, app/account use, process control, cleanup:
- Network mutations / external actions / publication permissions (default none):
- Tracked / untracked / submodule state before work:
- Toolchain / dependencies / configured tree / resolved executable (if needed):
- Capabilities: available / absent / unverified / not needed:
- Isolated fixtures, sensitive evidence location, redaction and action allowlist:

## Facts, design, and risks

- Facts with exact source locations:
- Assumptions to verify / unknowns:
- Entry points, owners, callers, consumers, and applicable domain rules:
- State transitions, ownership/thread/cancellation boundaries:
- Existing helper considered / smallest proposed change / alternatives rejected:
- Persistence, account isolation, trust, UI, dependency, performance impacts:
- Review matrix applicability and independent assessment result:

## Acceptance and checks

Define before implementation; use stable IDs. Add rows only for relevant claims.
A check may cover multiple acceptance/risk IDs; no required criterion is orphaned.

| Acceptance/risk ID | Input/state and required behavior | Negative case / invariant | Check IDs |
|---|---|---|---|
| AC-1 | <observable result> | <failure that must not occur> | CHK-1 |

| Check ID | Acceptance/risk IDs | Instrument / exact command and cwd | Oracle / control / falsifier | Prerequisites and bound |
|---|---|---|---|---|
| CHK-1 | AC-1 | <source from checks.md or inspected implementation> | <explicit pass/fail> | <capability, permission, timeout> |

## Candidate and execution evidence

- Baseline identity / observed baseline failure or missing red evidence:
- Final retained diff identity and scope, including submodule changes:
- Build configuration / artifact identity / overlay inventory and identity:
- Runner-owned candidate/evidence reference, if available:
- Frozen candidate / evidence invalidated by later edits / justified carry-forward:

Use the actual runner contract when available; do not invent receipt fields or
place commit hashes in queue artifacts that forbid them. Keep full output in
restricted approved storage; summaries point to evidence rather than replacing it.

| Check ID | Candidate reference | Expected / observed | Exit status | Full output / artifact paths | Outcome / reason |
|---|---|---|---|---|---|
| CHK-1 | <identity> | <not yet observed> | <not run> | <none yet> | NOT_RUN |

Outcomes: PASS, FAIL, NOT_RUN, BLOCKED, NOT_APPLICABLE (with concrete scope
reason). A required failed, missing, stale, skipped, or blocked check cannot
support readiness. Empty output and a completion marker alone are not passes.

## Review, failures, and handoff

- Independent reviewer results and exact candidate reviewed:
- Findings: location, trigger, mechanism, impact, evidence/confidence, correction,
  regression oracle; distinguish confirmed defects from hypotheses/options:
- Adjudication and source-backed reasons for rejected findings:
- Failure signatures and classification: implementation / instrument / capability:
- Repair attempts, invalidated reviews/checks, next direct instrument:
- Final retained paths / unintended changes or overlays / generated drift:
- Actual verified outputs; retained uninstrumented candidate coverage:
- Blockers / unverified platforms or risks / required human decision:
- Next valid action and its permissions:
- Readiness verdict (not permission to commit, publish, merge, or release):
