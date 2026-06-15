# Quality Flywheel Tool: Implementation Plan

Build the tool as a **local-first quality sidecar** around your coding agent.

Codex remains responsible for editing code. Your tool is responsible for:

```text
Capture → Verify → Review → Diagnose → Reproduce → Experiment → Release → Monitor
```

The central unit is not merely a prompt. It is a complete **run record**:

[
\text{Run} =
\text{prompt}
+\text{repository state}
+\text{agent trajectory}
+\text{resulting diff}
+\text{verification evidence}
+\text{human outcome}
]

OpenAI’s current Codex CLI supports non-interactive runs with JSONL output containing thread, turn, command, file-change, tool, error, and token-usage events. That makes `codex exec --json` the appropriate integration point for the first version. ([OpenAI Developers][1])

---

## 1. Product scope

### Primary objectives

The tool should answer:

1. How often does the agent complete tasks correctly?
2. Which kinds of tasks fail?
3. At what point in the trajectory do they fail?
4. Which model, prompt, tool, or configuration was involved?
5. Are our verifiers strong enough to reject incorrect patches?
6. Did a proposed improvement fix the intended cluster?
7. Did that improvement introduce regressions elsewhere?
8. Are previously fixed failures recurring in real usage?

### Non-objectives for the first version

Do not initially build:

* A replacement IDE
* Automatic prompt optimization
* Fully automated root-cause diagnosis
* A hosted multi-user observability platform
* An LLM-generated quality score
* A complex benchmark leaderboard
* A custom Codex client using the App Server

Those can come later. The first useful product is a CLI wrapper, SQLite database, regression runner, and review interface.

---

# 2. Recommended architecture

```text
┌─────────────────────────────────────────────────────┐
│ User                                                │
│                                                     │
│ aq run "Fix the parser..."                          │
│ or IDE command invoking aq                          │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│ Run Orchestrator                                    │
│                                                     │
│ • Creates run ID                                    │
│ • Captures Git/config state                         │
│ • Starts Codex                                      │
│ • Streams and stores events                         │
│ • Captures final diff                               │
└───────────────┬──────────────────┬──────────────────┘
                │                  │
                ▼                  ▼
┌───────────────────────┐  ┌──────────────────────────┐
│ Codex Adapter         │  │ Event Normalizer         │
│                       │  │                          │
│ codex exec --json     │  │ JSONL → internal events │
│ Later: SDK/App Server │  │ commands, edits, errors  │
└───────────────────────┘  └─────────────┬────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────┐
│ Verification Engine                                 │
│                                                     │
│ • Acceptance tests                                  │
│ • Full regression suite                             │
│ • Lint/type/build checks                            │
│ • Protected-path checks                             │
│ • Trajectory checks                                 │
│ • Known-bad-patch checks                            │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│ Review and Diagnosis                                │
│                                                     │
│ • Accept / partial / reject                         │
│ • Critical failure step                             │
│ • Root-cause labels                                 │
│ • Severity and confidence                           │
│ • Promote failure to regression case                │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│ Analytics and Experimentation                       │
│                                                     │
│ • Quality dashboard                                 │
│ • Failure clusters                                  │
│ • Baseline versus candidate experiments             │
│ • Regression gates                                  │
│ • Production recurrence monitoring                  │
└─────────────────────────────────────────────────────┘
```

---

# 3. Integration strategy

## Version 1: Wrap `codex exec`

Use a command such as:

```bash
aq run \
  --repo ~/projects/example \
  --profile default \
  --verify .agent-quality/verify.yaml \
  "Preserve empty query parameters without changing parameter order"
```

Internally:

```bash
codex exec \
  --json \
  --sandbox workspace-write \
  "Preserve empty query parameters without changing parameter order"
```

Codex’s non-interactive mode emits machine-readable JSONL events and supports explicit sandbox settings. The default sandbox is read-only, so the wrapper should explicitly request workspace write access only for runs that need edits. ([OpenAI Developers][1])

### Why this is the correct first integration

It gives you:

* Machine-readable events
* Prompt capture
* Commands and outputs
* File-change events
* Token usage
* Error events
* Agent final response
* Low implementation complexity

It also allows users to continue reviewing changes in their normal IDE.

The Codex CLI and IDE extension share configuration layers, including user and project configuration, models, approval settings, sandbox settings, and MCP configuration. This means wrapper-based runs can broadly follow the same project configuration as IDE sessions. ([OpenAI Developers][2])

## Version 2: Codex SDK

Move to the SDK when you need:

* Programmatic multi-turn sessions
* Better typed event handling
* CI integration
* Task queues
* Parallel evaluation runs
* Explicit thread continuation
* Per-turn sandbox changes

The current Codex SDK supports Python and TypeScript, thread creation and resumption, and configurable sandbox presets. ([OpenAI Developers][3])

## Version 3: App Server or IDE extension

Use Codex App Server only when you want a deeply integrated client:

* IDE sidebar
* Live streamed agent events
* Approval UI
* Thread browsing
* Interactive steering
* Custom review forms
* Branching or resuming conversations

The App Server is the same class of interface used to power rich clients such as the Codex VS Code extension. It exposes threads, turns, items, commands, file changes, and streamed notifications through JSON-RPC. ([OpenAI Developers][4])

---

# 4. Repository structure

Create a separate tool repository:

```text
agent-quality/
├── pyproject.toml
├── src/
│   └── agent_quality/
│       ├── cli.py
│       ├── config.py
│       ├── db.py
│       ├── models.py
│       ├── orchestrator.py
│       ├── adapters/
│       │   ├── base.py
│       │   └── codex_cli.py
│       ├── capture/
│       │   ├── git_state.py
│       │   ├── event_parser.py
│       │   └── artifacts.py
│       ├── verification/
│       │   ├── runner.py
│       │   ├── commands.py
│       │   ├── protected_paths.py
│       │   ├── trajectory.py
│       │   └── mutation_checks.py
│       ├── review/
│       │   ├── labels.py
│       │   └── service.py
│       ├── regressions/
│       │   ├── registry.py
│       │   ├── worktrees.py
│       │   └── runner.py
│       ├── experiments/
│       │   ├── definitions.py
│       │   ├── runner.py
│       │   └── statistics.py
│       └── reports/
│           ├── metrics.py
│           └── dashboard.py
├── migrations/
├── tests/
└── examples/
```

Each project being measured gets:

```text
project/
├── .agent-quality/
│   ├── config.yaml
│   ├── verify.yaml
│   ├── protected-paths.txt
│   └── cases/
├── .codex/
│   └── config.toml
├── AGENTS.md
└── application code
```

---

# 5. Data model

Do not store everything in one `runs` table. Separate observations, automated outcomes, and human judgments.

## Sessions

A session groups multiple corrective prompts.

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    repository_path TEXT NOT NULL,
    repository_remote_hash TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    final_outcome TEXT,
    task_summary TEXT
);
```

## Runs

One user prompt and the agent work that follows.

```sql
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    turn_number INTEGER NOT NULL DEFAULT 1,

    prompt TEXT,
    prompt_hash TEXT NOT NULL,

    repository_path TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    resulting_commit TEXT,

    model TEXT,
    agent_adapter TEXT NOT NULL,
    agent_version TEXT,
    wrapper_version TEXT,

    codex_config_hash TEXT,
    agents_md_hash TEXT,
    verifier_version TEXT,

    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER,

    agent_status TEXT NOT NULL,
    verifier_status TEXT,
    human_status TEXT,
    lifecycle_status TEXT,

    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,

    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
```

## Events

Store normalized events and preserve the original JSON.

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,

    event_type TEXT NOT NULL,
    item_type TEXT,
    status TEXT,

    command TEXT,
    exit_code INTEGER,
    path TEXT,

    normalized_payload TEXT,
    raw_payload TEXT NOT NULL,
    occurred_at TEXT,

    FOREIGN KEY(run_id) REFERENCES runs(id)
);
```

## Artifacts

```sql
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
```

Artifact types should include:

```text
prompt
events_jsonl
stderr
final_response
before_status
after_status
agent_patch
final_patch
verifier_log
environment_manifest
```

## Verifier results

```sql
CREATE TABLE verifier_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,

    verifier_name TEXT NOT NULL,
    verifier_category TEXT NOT NULL,

    command TEXT,
    started_at TEXT,
    duration_ms INTEGER,
    exit_code INTEGER,

    passed INTEGER NOT NULL,
    stdout_path TEXT,
    stderr_path TEXT,

    FOREIGN KEY(run_id) REFERENCES runs(id)
);
```

Verifier categories:

```text
acceptance
regression
build
lint
typecheck
security
protected_path
trajectory
resource_budget
custom
```

## Human reviews

```sql
CREATE TABLE human_reviews (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,

    outcome TEXT NOT NULL,
    code_retention TEXT,
    severity TEXT,

    primary_failure_category TEXT,
    contributing_categories TEXT,
    confidence REAL,

    critical_event_sequence INTEGER,
    notes TEXT,

    reviewed_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
```

## Failure clusters

```sql
CREATE TABLE failure_clusters (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,

    primary_category TEXT,
    severity TEXT,
    status TEXT NOT NULL,

    first_seen_at TEXT,
    last_seen_at TEXT,
    occurrence_count INTEGER DEFAULT 0,

    proposed_intervention TEXT,
    linked_regression_case TEXT
);
```

---

# 6. Run lifecycle

Use an explicit state machine:

```text
CREATED
  ↓
PREPARING
  ↓
AGENT_RUNNING
  ↓
AGENT_COMPLETED
  ↓
VERIFYING
  ↓
REVIEW_PENDING
  ↓
FINALIZED
```

Error states:

```text
PREPARATION_FAILED
AGENT_FAILED
AGENT_TIMED_OUT
VERIFIER_FAILED
REVIEW_SKIPPED
```

## Before the agent starts

Capture:

```text
Repository root
Current branch
HEAD commit
Git status
Untracked files
Diff before run
Language/runtime versions
Dependency lockfile hashes
AGENTS.md hash
.codex/config.toml hash
Wrapper version
Codex version
Model
Sandbox policy
Verifier definition hash
```

Reject or warn about a dirty repository unless the user explicitly allows it.

A clean starting state makes attribution much easier.

## While the agent is running

Capture:

* Every JSONL event
* Command start and completion
* Exit codes
* Files changed
* Tool calls
* Agent messages
* Errors
* Token usage
* Timing
* Repeated command patterns

Do not parse human-readable terminal output when a structured event is available.

## After the agent finishes

Capture:

```bash
git status --porcelain=v1
git diff --binary
git diff --stat
git diff --name-status
```

Then run verification independently.

The agent’s statement that tests passed is not verification.

---

# 7. Outcome model

Do not reduce a run to a single Boolean.

Use four separate dimensions.

## Agent execution status

```text
completed
failed
timed_out
cancelled
```

## Automated verification status

```text
passed
failed
not_configured
infrastructure_error
```

## Human outcome

```text
accepted_cleanly
accepted_with_minor_edits
accepted_with_major_edits
partial
rejected
reverted_later
not_reviewed
```

## Lifecycle outcome

```text
merged
discarded
superseded
still_open
production_incident
```

This exposes important distinctions:

| Agent     | Verifier | Human    | Interpretation                                |
| --------- | -------- | -------- | --------------------------------------------- |
| Completed | Passed   | Accepted | Confirmed success                             |
| Completed | Failed   | Rejected | Normal detected failure                       |
| Completed | Passed   | Rejected | Weak verifier or subjective mismatch          |
| Completed | Failed   | Accepted | Verifier may be broken or overly strict       |
| Failed    | Not run  | Partial  | Agent process failed but produced useful work |

---

# 8. Verification configuration

Use repository-level YAML:

```yaml
version: 1

environment:
  timeout_seconds: 1200
  network: disabled

acceptance:
  - name: requested-behavior
    command: pytest -q tests/evaluator/test_empty_query.py
    timeout_seconds: 120

regression:
  - name: unit-suite
    command: pytest -q tests/unit
    timeout_seconds: 300

  - name: integration-suite
    command: pytest -q tests/integration
    timeout_seconds: 600

static:
  - name: lint
    command: ruff check .

  - name: typing
    command: mypy src/

protected_paths:
  - tests/evaluator/**
  - .github/**
  - scripts/release/**
  - .agent-quality/**

trajectory:
  require_test_after_final_edit: true
  max_identical_failed_commands: 1
  prohibit_claimed_unobserved_commands: true

budgets:
  max_duration_seconds: 1200
  max_changed_files: 20
  max_added_lines: 1000
  max_output_tokens: 50000
```

## Verifier categories

### Acceptance verifier

Did the requested behavior get implemented?

### Regression verifier

Did existing behavior remain intact?

### Static verifier

Did lint, formatting, typing, or compilation succeed?

### Constraint verifier

Did the agent avoid forbidden paths, dependencies, or APIs?

### Trajectory verifier

Did the agent follow a reliable process?

### Budget verifier

Was the result achieved within acceptable cost and scope?

### Human verifier

Does the result actually satisfy the user’s intent?

---

# 9. Trajectory-level tests

Trajectory tests should be normal executable assertions.

Examples:

```python
def test_successful_test_was_run_after_final_edit(events):
    final_edit = max(
        event.sequence_number
        for event in events
        if event.item_type == "file_change"
    )

    successful_later_tests = [
        event
        for event in events
        if event.sequence_number > final_edit
        and event.item_type == "command_execution"
        and event.exit_code == 0
        and looks_like_test_command(event.command)
    ]

    assert successful_later_tests
```

```python
def test_failed_command_is_not_repeated_indefinitely(events):
    failures = {}

    for event in events:
        if event.item_type != "command_execution":
            continue
        if event.exit_code in (None, 0):
            continue

        normalized = normalize_command(event.command)
        failures[normalized] = failures.get(normalized, 0) + 1

    assert max(failures.values(), default=0) <= 2
```

Other checks:

* Agent inspected a file before modifying it.
* Test execution occurred after the last code edit.
* Protected files were never touched.
* Agent did not report a command that was not executed.
* Agent did not silently ignore a failed verifier.
* Agent did not repeatedly retry an identical failed action.
* Agent did not exceed the permitted scope.
* Agent did not use network access in an offline task.

These are not substitutes for output correctness. They measure process reliability.

---

# 10. Human review interface

The first interface can be terminal-based:

```text
Run: 01JX...
Automatic verification: PASSED
Files changed: 4
Lines added: 86
Lines removed: 31
Duration: 7m 14s
Tokens: 43,201

Human outcome:
  [1] Accepted cleanly
  [2] Accepted with minor edits
  [3] Accepted with major edits
  [4] Partial
  [5] Rejected
  [6] Review later
```

For non-clean acceptance:

```text
Primary failure category:
  [1] Specification
  [2] Context acquisition
  [3] Fault localization
  [4] Planning
  [5] Implementation
  [6] Tool use
  [7] Verification
  [8] Environment
  [9] Scope control
  [10] Reporting
  [11] Unknown
```

Then:

```text
Contributing categories:
Severity:
Confidence:
Critical event:
Notes:
Promote to regression candidate? [y/N]
```

Do not force certainty. `unknown` and low-confidence labels are valid.

---

# 11. Failure taxonomy

Use a small stable top-level taxonomy:

| Category           | Definition                                             |
| ------------------ | ------------------------------------------------------ |
| Specification      | Misunderstood or omitted a requirement                 |
| Context            | Failed to locate or retain relevant information        |
| Fault localization | Edited the wrong component                             |
| Planning           | Chose an unsuitable implementation approach            |
| Implementation     | Correct approach, incorrect code                       |
| Tool use           | Incorrect tool, parameters, or recovery behavior       |
| Verification       | Inadequate or misleading validation                    |
| Environment        | Dependency, runtime, permissions, or platform mismatch |
| Scope control      | Changed too much or touched unrelated code             |
| Reporting          | Final claim did not match actual evidence              |
| Unknown            | Evidence is insufficient                               |

For each failure, record:

```json
{
  "primary": "specification",
  "contributing": ["context", "verification"],
  "confidence": 0.65,
  "critical_event": 18,
  "summary": "The ordering requirement was omitted before code search began."
}
```

The **critical event** should be the earliest consequential mistake after which recovery became unlikely.

---

# 12. Regression-case registry

Store regression cases in Git:

```text
.agent-quality/cases/
└── preserve-empty-query-values/
    ├── case.yaml
    ├── prompt.md
    ├── setup.sh
    ├── verify.sh
    ├── reference.patch
    ├── known-bad/
    │   ├── drops-empty-values.patch
    │   ├── loses-order.patch
    │   ├── modifies-tests.patch
    │   └── catches-all-errors.patch
    └── variants/
        ├── repeated-values.yaml
        ├── large-file.yaml
        └── decoy-function.yaml
```

## Case definition

```yaml
id: preserve-empty-query-values
version: 1

repository: example-service
base_commit: 4f92c3a

source:
  type: production_failure
  run_id: 01JXABC
  date: 2026-06-15

task:
  prompt_file: prompt.md

verification:
  script: verify.sh

expected:
  acceptance_required: true
  regression_required: true
  protected_paths_clean: true

repetitions: 3

tags:
  - parser
  - edge-case
  - requirement-omission
```

## Execution isolation

For every regression run:

1. Create a temporary Git worktree at `base_commit`.
2. Apply setup steps.
3. Start Codex in the temporary worktree.
4. Capture the full trajectory.
5. Run the verifier.
6. Save the patch and result.
7. Destroy the worktree.

Never run the suite repeatedly in the user’s active working tree.

---

# 13. Evaluate the verifier

Every important case must include known-bad patches.

Run:

```text
Unmodified base repository → must fail
Reference patch            → must pass
Known-bad patch 1          → must fail
Known-bad patch 2          → must fail
Known-bad patch 3          → must fail
```

Define:

[
\text{bad-patch rejection rate}
===============================

\frac{\text{known-bad patches rejected}}
{\text{known-bad patches evaluated}}
]

A case should not be considered trustworthy if one of its deliberately incorrect patches passes.

Known-bad patches should represent realistic agent shortcuts:

* Visible example only
* Broad exception swallowing
* Test deletion
* Hard-coded fixture
* Incorrect ordering
* Incorrect null behavior
* Backward incompatibility
* Generated-file-only fix
* Dependency upgrade without lockfile update

---

# 14. Generate regression families

Do not retain only the original failing example.

For every promoted failure, produce:

1. Exact reproduction
2. Minimal synthetic version
3. Neighboring edge case
4. Stress variant
5. Negative control
6. Known-bad patch set

Example:

```text
Original:
  Preserve a=

Variants:
  Preserve a=&b=1
  Preserve repeated a=&a=1
  Preserve encoded whitespace
  Preserve order across repeated values
  Large parser file with irrelevant code
  Similar decoy parser function
  Explicit task where empty values should be removed
```

This reduces memorization and prompt overfitting.

---

# 15. Failure clustering workflow

## Start manually

For the first set of failures:

1. Review the trajectory.
2. Mark the critical event.
3. Assign primary and contributing categories.
4. Write a one-sentence normalized summary.
5. Link it to an existing cluster or create a new one.

Example normalized summary:

```text
Agent declared completion after running only a targeted unit test;
the full integration suite later failed because a shared caller was broken.
```

## Later automation

Once you have enough labeled failures, automate candidate grouping using:

* Same failing verifier
* Same edited subsystem
* Same exception
* Similar normalized summary
* Same critical-event pattern
* Semantic embedding similarity

The system should propose clusters, not silently decide them.

## Cluster priority

Use:

[
\text{Priority}
===============

\text{frequency}
\times
\text{severity}
\times
\text{diagnostic confidence}
\times
\text{fixability}
]

Add a recency multiplier when necessary:

[
\text{Priority}_{r}
===================

\text{Priority}
\times
\text{recency weight}
]

---

# 16. Harness versioning

Treat the complete agent environment as the harness:

```text
Model
System instructions
AGENTS.md
.codex/config.toml
Tools
Tool schemas
MCP servers
Sandbox policy
Approval policy
Verification commands
Completion requirements
Wrapper version
```

OpenAI’s improvement-loop cookbook similarly treats instructions, tools, routing, output requirements, and validation checks as parts of the harness, with traces and feedback converted into reusable evaluations before changes are proposed. ([OpenAI Developers][5])

Create a harness fingerprint:

```python
harness_hash = sha256(
    model
    + agents_md_contents
    + codex_config_contents
    + tool_schema_json
    + verifier_yaml
    + wrapper_version
)
```

Every run must reference it.

Without this, score changes cannot be attributed.

---

# 17. Experiment framework

Each proposed improvement becomes an explicit experiment.

Example:

```yaml
id: require-post-edit-verification

hypothesis: >
  Requiring a successful test command after the final edit will
  reduce completion-without-verification failures.

baseline:
  harness: harness-v12

candidate:
  harness: harness-v13

target_clusters:
  - completion-without-verification

suite:
  target_cases: 15
  regression_cases: 80
  holdout_cases: 20

repetitions: 3

release_gates:
  target_success_delta_min: 0.10
  overall_regression_max: 0.02
  critical_regressions_allowed: 0
  token_cost_increase_max: 0.10
```

## Paired evaluation

Run the same case under both harnesses:

```text
Case 1:
  Baseline run 1
  Candidate run 1
  Baseline run 2
  Candidate run 2
  Baseline run 3
  Candidate run 3
```

Compare:

* Success
* Consistency
* Tokens
* Duration
* Changed files
* Verification behavior
* Human acceptance

## Release decision

Promote a candidate only when:

1. The target cluster improves.
2. The full regression suite does not materially degrade.
3. Critical safety and destructive-failure cases remain clean.
4. Fresh holdout performance does not decline.
5. Cost and latency remain acceptable.

---

# 18. Metrics dashboard

## Outcome metrics

### Verified pass rate

[
\frac{\text{runs passing automatic verification}}
{\text{completed runs}}
]

### Human acceptance rate

[
\frac{\text{accepted runs}}
{\text{reviewed runs}}
]

### Clean acceptance rate

[
\frac{\text{accepted without edits}}
{\text{reviewed runs}}
]

### False-confidence rate

[
\frac{\text{verifier passed and human rejected}}
{\text{verifier-passed reviewed runs}}
]

### Regression escape rate

[
\frac{\text{accepted runs later reverted or linked to incidents}}
{\text{accepted runs}}
]

## Reliability metrics

### Success@1

Success probability for a single attempt.

### Stable-pass rate

[
\frac{\text{cases passing every repeated run}}
{\text{cases evaluated}}
]

### Mean attempts to acceptance

Useful for multi-turn sessions.

## Efficiency metrics

* Tokens per accepted task
* Duration per accepted task
* Commands per accepted task
* Files inspected before first correct edit
* Repeated failed commands
* Changed lines per accepted task
* Human edit volume after agent completion

## Flywheel metrics

* Failures promoted into regression cases
* Time from incident to regression case
* Recurrence rate for fixed clusters
* Percentage of regression cases with known-bad patches
* Bad-patch rejection rate
* Target-cluster improvement per harness release
* Number of stale or retired cases

---

# 19. Multi-turn session measurement

Track sessions separately from turns.

Example:

```text
Turn 1: Implement feature
Turn 2: Use the existing abstraction
Turn 3: Fix integration tests
Turn 4: Revert unrelated refactor
Turn 5: Handle empty values
```

Session metrics:

* Number of corrective prompts
* Time to final acceptance
* Total tokens
* Number of regressions introduced after an earlier passing state
* Files repeatedly rewritten
* Requirements contradicted across turns
* Percentage of the first patch retained
* Final human edit volume

A tool that measures only isolated prompts will miss degradation across corrective conversations.

---

# 20. Production monitoring

For a local user, “production traffic” means everyday development work.

Capture weak failure signals:

```text
Patch rejected
Patch reverted
User asks agent to undo unrelated changes
CI fails after agent claims success
User manually rewrites most of the patch
Agent repeatedly runs the same failing command
Agent times out
Agent changes excessive files
Agent modifies protected paths
Agent claims tests passed without evidence
```

Add commands such as:

```bash
aq mark-reverted <run-id>
aq link-ci-failure <run-id> <log-file>
aq mark-incident <run-id> --severity high
aq promote <run-id> --case-id parser-empty-values
```

A cluster should reopen automatically when a supposedly fixed failure recurs.

---

# 21. Security and privacy

Store data locally by default:

```text
~/.agent-quality/
├── quality.sqlite3
├── artifacts/
├── cases/
├── experiments/
└── reports/
```

Implement:

* Prompt redaction rules
* Secret-pattern scanning
* Environment-variable allowlists
* Configurable retention
* Repository path hashing
* Optional prompt hashing instead of plaintext storage
* Encryption for artifact archives
* Explicit export commands
* No automatic cloud upload
* No storage of authentication files
* Network-disabled evaluation environments where possible

Do not capture all environment variables. Record an allowlisted manifest such as runtime versions and dependency hashes.

---

# 22. CLI design

## Running tasks

```bash
aq run "Fix the parser"
aq run --verify fast "Fix the parser"
aq run --session existing-session "Now add integration tests"
```

## Reviewing

```bash
aq review
aq review <run-id>
aq show <run-id>
aq diff <run-id>
aq trace <run-id>
```

## Regressions

```bash
aq promote <run-id>
aq case validate preserve-empty-query-values
aq case run preserve-empty-query-values
aq suite run regression
```

## Experiments

```bash
aq experiment create
aq experiment run require-post-edit-verification
aq experiment report require-post-edit-verification
```

## Reports

```bash
aq report summary
aq report failures
aq report clusters
aq report reliability
aq report cost
```

---

# 23. Implementation sequence

## Stage 1 — Instrumented wrapper

Deliver:

* `aq run`
* Codex JSONL capture
* Git state capture
* SQLite storage
* Patch storage
* Configurable verifier commands
* Terminal review prompt
* Basic summary report

Exit criteria:

```text
At least 20 real tasks captured
All events linked to runs
Diffs and verifier logs reproducible
Human labels stored separately from automatic outcomes
No lost or corrupted run records
```

## Stage 2 — Reliable verification

Add:

* Protected paths
* Test-after-final-edit assertion
* Timeouts
* Build/lint/type categories
* Dirty-tree handling
* Independent verifier container or worktree
* False-confidence report

Exit criteria:

```text
Automatic verifier can be rerun independently
Protected-file edits are detected
Agent claims are cross-checked against observed commands
Verifier failures are distinguished from infrastructure failures
```

## Stage 3 — Regression registry

Add:

* Case format
* Git worktree runner
* Exact failure replay
* Repeated trials
* Known-bad patch validation
* Regression-family variants

Exit criteria:

```text
At least 10 meaningful regression cases
Every critical case has known-bad patches
Cases run from clean base commits
Results are reproducible
```

## Stage 4 — Failure diagnosis

Add:

* Critical-event annotation
* Primary and contributing categories
* Confidence score
* Cluster management
* Frequency/severity prioritization
* Representative-trace selection

Exit criteria:

```text
Top recurring failure clusters identified
Every high-severity failure has an owner or disposition
Low-confidence diagnoses remain explicitly uncertain
```

## Stage 5 — Experimentation

Add:

* Harness fingerprints
* Baseline versus candidate runs
* Paired repeated trials
* Holdout cases
* Cost and latency comparisons
* Release gates

Exit criteria:

```text
A harness change cannot be promoted without regression evidence
Target-cluster improvement is measured separately
Critical regressions block release
```

## Stage 6 — IDE integration

Add:

* IDE command or task
* Run status view
* Review form
* Trace timeline
* Cluster links
* “Promote to regression” button

Use the Codex SDK or App Server at this stage rather than attempting to reverse-engineer the existing IDE interface. The SDK is suited to programmatic internal workflows, while App Server is intended for rich client integration. ([OpenAI Developers][4])

---

# 24. Recommended technology choices

For a local first implementation:

| Component            | Recommendation                    |
| -------------------- | --------------------------------- |
| Language             | Python 3.11+                      |
| CLI                  | Typer                             |
| Data models          | Pydantic                          |
| Database             | SQLite                            |
| Migrations           | Alembic                           |
| Configuration        | YAML                              |
| Process execution    | `asyncio.create_subprocess_exec`  |
| Dashboard            | Streamlit initially               |
| Statistical analysis | pandas/scipy                      |
| Worktree isolation   | Git worktrees                     |
| Artifact hashing     | SHA-256                           |
| Packaging            | `uv` or standard Python packaging |
| Testing              | pytest                            |

Use PostgreSQL only when multiple users or machines need concurrent access.

---

# 25. Definition of a successful MVP

The MVP is complete when you can demonstrate this loop:

```text
1. Run a real Codex task through the wrapper.
2. Capture its prompt, configuration, trajectory, diff, and tokens.
3. Independently run acceptance and regression checks.
4. Record a human acceptance decision.
5. Diagnose a failed run and mark its critical event.
6. Promote that run into a reproducible regression case.
7. Add neighboring variants and a known-bad patch.
8. Change one harness component.
9. Compare baseline and candidate versions.
10. Reject or promote the change using explicit release gates.
11. Detect whether that failure recurs in later normal usage.
```

That is the smallest implementation that genuinely constitutes a quality flywheel rather than a prompt logger.

The immediate build order should therefore be:

```text
Codex wrapper
→ structured run database
→ independent verifier
→ human review
→ regression registry
→ failure clustering
→ harness experiments
→ IDE integration
```

The most common mistake would be starting with the dashboard or automated clustering. The highest-value foundation is reproducible evidence: clean repository state, structured trajectories, independent verification, and consistent human outcomes.

[1]: https://developers.openai.com/codex/noninteractive?utm_source=chatgpt.com "Non-interactive mode – Codex"
[2]: https://developers.openai.com/codex/config-basic?utm_source=chatgpt.com "Config basics – Codex"
[3]: https://developers.openai.com/codex/sdk?utm_source=chatgpt.com "Codex SDK"
[4]: https://developers.openai.com/codex/app-server?utm_source=chatgpt.com "Codex App Server"
[5]: https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop?utm_source=chatgpt.com "Build an Agent Improvement Loop with Traces, Evals, and ..."
