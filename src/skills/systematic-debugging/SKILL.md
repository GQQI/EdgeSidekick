---
name: systematic-debugging
description: >
  Find root cause before proposing fixes. Use when encountering a bug,
  test failure, unexpected behavior, performance problem, or build
  failure. Do not use for green-field feature design or document work.
license: Complete terms in LICENSE.txt
---

# Systematic Debugging

## Overview

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

## EdgeSidekick / 离线适配

- 全程离线。用 `read_file`、`run_shell`、测试命令收集证据，不要猜。
- 参考文件：`root-cause-tracing.md`、`defense-in-depth.md`、`condition-based-waiting.md`（本目录）。
- 写回归测试时遵循 `test-driven-development`；宣称修好前遵循 `verification-before-completion`。
- 不要自动 commit。

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## When to Use

Use for ANY technical issue: test failures, production bugs, unexpected
behavior, performance problems, build failures, integration issues.

Use this ESPECIALLY when under time pressure, "just one quick fix"
seems obvious, you've already tried multiple fixes, or you don't fully
understand the issue.

## The Four Phases

You MUST complete each phase before proceeding to the next.

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read Error Messages Carefully**
   - Don't skip past errors or warnings
   - Read stack traces completely
   - Note line numbers, file paths, error codes

2. **Reproduce Consistently**
   - Can you trigger it reliably?
   - What are the exact steps?
   - If not reproducible → gather more data, don't guess

3. **Check Recent Changes**
   - Git diff, recent commits
   - New dependencies, config changes
   - Environmental differences

4. **Gather Evidence in Multi-Component Systems**

   BEFORE proposing fixes, add diagnostic instrumentation at each
   component boundary: log what enters, what exits, and the environment
   at that layer. Run once. Identify WHERE it breaks. Then investigate
   that component.

5. **Trace Data Flow**

   When the error is deep in the call stack, see `root-cause-tracing.md`.
   Quick version: where does the bad value originate? What called this
   with a bad value? Keep tracing up until you find the source. Fix at
   source, not at symptom.

### Phase 2: Pattern Analysis

1. Find working examples of similar code in the same codebase
2. If implementing a pattern, read the reference completely
3. List every difference between working and broken
4. Understand dependencies, config, and assumptions

### Phase 3: Hypothesis and Testing

1. Form a single hypothesis: "I think X is the root cause because Y"
2. Make the smallest possible change to test it — one variable
3. Did it work? Yes → Phase 4. No → new hypothesis. Don't stack fixes.
4. If you don't understand, say so. Don't pretend.

### Phase 4: Implementation

1. Create a failing test case first (simplest reproduction)
2. Implement a single fix for the identified root cause
3. Verify: original symptom gone, other tests still pass
4. If the fix doesn't work: STOP. Count attempts. If < 3, return to Phase 1.
   If ≥ 3, question the architecture with the user before Fix #4.

## Red Flags — STOP and Follow Process

- "Quick fix for now, investigate later"
- "Just try changing X and see"
- "Add multiple changes, run tests"
- "Skip the test, I'll manually verify"
- "It's probably X"
- Proposing solutions before tracing data flow
- "One more fix" after 2+ failures

ALL of these mean: STOP. Return to Phase 1.

## Quick Reference

| Phase | Key Activities | Success Criteria |
|-------|----------------|------------------|
| 1. Root Cause | Read errors, reproduce, check changes, gather evidence | Understand WHAT and WHY |
| 2. Pattern | Find working examples, compare | Identify differences |
| 3. Hypothesis | Form theory, test minimally | Confirmed or new hypothesis |
| 4. Implementation | Create test, fix, verify | Bug resolved, tests pass |

## When Process Reveals "No Root Cause"

If investigation shows the issue is truly environmental, timing-dependent,
or external: document what you checked, add handling (retry / timeout /
error message) and monitoring. 95% of "no root cause" cases are incomplete
investigation.
