---
name: verification-before-completion
description: >
  Run verification commands and read the output before claiming work is
  complete, fixed, or passing. Use when about to say a bug is fixed,
  tests pass, a build succeeded, or a task is done. Do not use for
  purely conversational answers with no runnable check.
license: Complete terms in LICENSE.txt
---

# Verification Before Completion

## Overview

**Core principle:** Evidence before claims, always.

## EdgeSidekick / 离线适配

- 用 `run_shell` 跑项目里已有的测试 / lint / 构建命令，完整读输出。
- 没有可运行检查时（纯文案、设计讨论），用对照清单逐条核对，不要假装跑过测试。
- 不要把上一次运行的结果当成这一次的证据。

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this turn, you cannot claim it passes.

## The Gate Function

BEFORE claiming any status or expressing satisfaction:

1. IDENTIFY: What command or checklist proves this claim?
2. RUN: Execute the full command (fresh, complete), or walk the checklist
3. READ: Full output, exit code, failure count
4. VERIFY: Does the output confirm the claim?
   - If NO: State the actual status with evidence
   - If YES: State the claim WITH evidence
5. ONLY THEN: Make the claim

Skip any step = not verified.

## Common Failures

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Linter clean | Linter output: 0 errors | Partial check |
| Build succeeds | Build command: exit 0 | Linter passing |
| Bug fixed | Original symptom re-tested | Code changed, assumed fixed |
| Requirements met | Line-by-line checklist | Tests passing |

## Red Flags — STOP

- Using "should", "probably", "seems to"
- "Great!", "Perfect!", "Done!" before verification
- Trusting a subagent success report without checking
- Partial verification
- ANY wording implying success without having run verification

## Key Patterns

Tests:

- Good: run the test command, see 34/34 pass, then say "all tests pass"
- Bad: "should pass now" / "looks correct"

Bug fix:

- Good: re-run the original failing case and show it now passes
- Bad: "I changed the code, so it's fixed"

Requirements:

- Good: re-read the plan, tick each item, report gaps or completion
- Bad: "tests pass, phase complete"
