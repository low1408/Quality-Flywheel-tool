Source B contains several useful additions. Much of it overlaps with the earlier design, but the following points should be incorporated.

## 1. Test the verifier itself with known-bad patches

This is the strongest point I underemphasized.

Running tests tells you only that the patch satisfied the current test suite. It does not establish that the suite distinguishes correct behavior from plausible incorrect behavior.

For every important regression case, maintain:

```text
regression-case/
├── task.md
├── verify.sh
├── correct.patch
└── known-bad/
    ├── handles-visible-example-only.patch
    ├── swallows-all-exceptions.patch
    ├── changes-the-test.patch
    └── breaks-backward-compatibility.patch
```

Your validation should assert:

```text
Original repository       → verifier fails
Correct reference patch   → verifier passes
Every known-bad patch     → verifier fails
```

You can also use mutation-testing tools such as `mutmut`, Cosmic Ray, or Stryker to generate small incorrect variants automatically.

Source B correctly identifies this as a second-order evaluation problem: you must evaluate whether your evaluator is strong enough, not just whether the agent passes it. 

A useful metric is:

[
\text{bad-patch rejection rate}
===============================

\frac{\text{known-bad patches rejected}}
{\text{known-bad patches tested}}
]

Do not promote an important regression case unless this is 100% for your curated bad patches.

## 2. Make trajectory assertions first-class regression tests

I included some process checks, but Source B frames this more clearly.

A regression test can verify not only the final repository but also how the agent reached it.

For example:

```python
def test_agent_verified_after_last_edit(events):
    final_edit_index = max(
        i for i, event in enumerate(events)
        if event["type"] == "file_change"
    )

    later_tests = [
        event
        for event in events[final_edit_index + 1 :]
        if event["type"] == "command"
        and event["purpose"] == "test"
        and event["exit_code"] == 0
    ]

    assert later_tests, "No successful test run after final code edit"
```

Other useful trajectory assertions include:

```text
Relevant tests were run after the final edit
No identical failed command was repeated more than once
The agent inspected the target implementation before editing it
Protected files were never written
The agent did not claim a command ran when it did not
The task stayed within a token or step budget
A failed narrow test was not followed by a false completion claim
```

This is especially valuable for prompt and tool-policy changes. Two agents might produce the same successful patch, but one followed a reliable process while the other succeeded accidentally. Source B’s “test call after edit with visible passing output” example is a good template. 

## 3. Create stress variants of regression cases

My earlier response recommended neighboring cases, but Source B gives a useful systematic form: transform an existing case to stress the particular capability being fixed.

For a context-retrieval failure, generate variants such as:

* Add realistic irrelevant files.
* Expand the relevant file from 300 to 3,000 lines.
* Move the implementation into a less obvious module.
* Add similarly named decoy functions.
* Split the implementation across multiple files.
* Rename files while keeping behavior unchanged.
* Reduce the available token or tool-call budget.

The agent should still resolve the task, and efficiency should not degrade uncontrollably.

This is effectively **metamorphic testing** for agents: modify aspects of the environment that should not change the correct outcome, then check whether the agent remains successful.

Store the relationship explicitly:

```yaml
id: empty-query-large-file
parent_case: empty-query-base
transformation: realistic-file-padding
expected_behavior: same
max_token_multiplier: 1.5
max_step_multiplier: 1.5
```

## 4. Do not assume one failure distribution applies to every model

Source B correctly emphasizes that failure clusters may differ substantially between models, tools, and agent configurations. 

Therefore, do not aggregate all failures into one pool. Slice reports by:

```text
model
model version
Codex version
system-prompt version
AGENTS.md version
tool configuration
repository
task type
context size
```

For example:

```text
Model A
  specification failures: 31%
  context failures:       12%
  tool-use failures:       8%

Model B
  specification failures: 14%
  context failures:       29%
  tool-use failures:      21%
```

A prompt change designed for Model A’s requirement omissions could increase unnecessary planning and cost for Model B without fixing its context-retrieval problems.

Your database should therefore associate every label with the exact harness configuration:

```sql
failure_labels(
    run_id,
    primary_category,
    secondary_category,
    confidence,
    reviewer,
    notes
)
```

## 5. Treat root-cause labels as uncertain

My earlier taxonomy looked more categorical than it should.

A run may fail because:

```text
The specification was misunderstood
        ↓
The wrong files were retrieved
        ↓
The implementation was incomplete
        ↓
The verification command was too narrow
```

Which one is “the root cause” is partly a judgment.

Use:

* A primary category
* Zero or more contributing categories
* A confidence score
* An `unknown` option
* Optional second-reviewer disagreement

Example:

```json
{
  "primary": "specification",
  "contributing": ["context", "verification"],
  "confidence": 0.65,
  "critical_step": 17,
  "notes": "Agent omitted ordering requirement before searching code."
}
```

For the first 50–100 failures, manual labeling is preferable. Later, an LLM can propose labels, but low-confidence cases should still be reviewed. Source B is right that similar observed behavior can arise from different causes, so fully automatic root-cause classification should not be treated as ground truth. 

## 6. Include long, multi-turn sessions

The initial wrapper was task-oriented: one prompt, one run, one result. Real IDE usage is often a sequence:

```text
Implement feature
→ No, use the existing abstraction
→ Fix the tests
→ That changed unrelated behavior
→ Revert that portion
→ Now handle this edge case
```

You need both **turn-level** and **session-level** measurements.

Add identifiers such as:

```sql
sessions(
    session_id,
    repo,
    started_at,
    final_outcome
);

runs(
    run_id,
    session_id,
    turn_number,
    prompt,
    parent_run_id,
    automatic_outcome,
    human_outcome
);
```

Useful session metrics include:

```text
Prompts required until acceptance
Number of user corrections
Number of files repeatedly edited
Regressions introduced after an earlier passing state
Tokens per completed session
Whether the final implementation retained the first patch
Requirement drift across turns
```

A coding agent that performs well on isolated prompts may still become confused or destructive after five corrective turns. Source B correctly points out that single-shot evaluations do not capture this degradation. 

## 7. Bootstrap your suite from historical PRs and tickets

I mentioned production replay, but Source B offers a practical starting method: mine previously completed work.

For each suitable historical PR:

```text
Base commit       = commit before the PR
Prompt            = issue or ticket description
Reference outcome = merged PR
FAIL_TO_PASS      = tests demonstrating the requested behavior
PASS_TO_PASS      = existing repository tests
```

Do not evaluate by exact similarity to the historical patch. There may be several valid implementations. Evaluate behavior, constraints, and regressions.

Good candidates are:

* Clear bug reports
* Small or medium feature requests
* Regressions with a known reproducer
* Tasks where the original PR added tests
* Tasks that do not depend heavily on undocumented human context

Poor candidates are:

* Mechanical dependency updates
* PRs whose issue descriptions do not state the real requirements
* Large redesigns decided through undocumented meetings
* Tasks whose tests depend on unavailable external services

This gives a regular user an internal benchmark without manually inventing dozens of synthetic exercises.

## 8. Aggregate repeated incidents into one tracked failure mode

The wrapper currently records individual failures. Source B adds a useful operational abstraction: multiple runs should become one recurring issue with a frequency and representative traces. 

For example:

```text
Cluster: Completion without post-edit verification

Occurrences: 14
Affected models: codex-X, codex-Y
Repositories: 4
First observed: 2026-05-03
Last observed: 2026-06-14
Estimated cost: 6 rejected patches
Representative runs:
  - run_0128
  - run_0181
  - run_0220
Regression cases:
  - verification-after-final-edit
Proposed intervention:
  - completion gate in wrapper
```

This should become the unit you prioritize and close, rather than treating 14 runs as unrelated rows.

A simple cluster table would be:

```sql
failure_clusters(
    cluster_id,
    title,
    status,
    occurrence_count,
    severity,
    first_seen,
    last_seen,
    proposed_fix,
    regression_case_id
);
```

## 9. Add benchmark maintenance and retirement

I discussed holdouts and contamination, but not enough about lifecycle management.

Each regression case should have:

```yaml
created_at: 2026-06-15
source: production
last_reviewed_at: 2026-06-15
owner: local
status: active
verifier_strength: 4_known_bad_patches_rejected
```

Periodically examine:

* Whether the task still represents your current codebase
* Whether the verifier still catches plausible incorrect patches
* Whether every model passes it trivially
* Whether the task has leaked into prompts or instructions
* Whether it should be made harder, replaced, or retired

Keep old cases for regression detection, but do not let hundreds of trivial cases dominate the headline score.

## What was already covered

Source B overlaps with the earlier design on:

* PASS_TO_PASS versus FAIL_TO_PASS tests
* Recording structured agent traces
* External verification
* Root-cause taxonomies
* Model/configuration versioning
* Full-suite regression gates
* Fresh holdouts
* Production replay
* Repeated trials
* Human acceptance labels
* False-confidence measurement
* Frequency-and-severity-based prioritization

The material additions are primarily:

1. **Adversarially evaluate the verifier with plausible bad patches.**
2. **Make trajectory-level behavior part of the regression contract.**
3. **Generate stress transformations of existing tasks.**
4. **Represent diagnosis uncertainty and multi-causal failures.**
5. **Evaluate long multi-turn IDE sessions, not only isolated tasks.**
6. **Maintain, strengthen, and retire benchmark cases over time.**

One caution: Source B includes many precise percentages, benchmark scores, model-specific findings, and recent-study claims without supplying the underlying references in the pasted material. The operational ideas are useful, but those numerical claims should not be used in documentation or decision-making until their primary sources are checked.
