---
name: writing-plans
description: >
  Write a bite-sized implementation plan from an approved spec or
  multi-step requirements, before touching code. Use when the user
  has a design/spec and wants a task list with files, tests, and
  verification steps. Do not use for single-file fixes or document-only work.
license: Complete terms in LICENSE.txt
---

# Writing Plans

## Overview

Write comprehensive implementation plans assuming the engineer has zero
context for the codebase. Document everything they need: which files to
touch, sample code, how to test. Bite-sized tasks. DRY. YAGNI. TDD.

## EdgeSidekick / 离线适配

- 计划写到当前 workspace 的 `docs/plans/YYYY-MM-DD-<topic>.md`（用户指定路径则听用户的）。
- 不要自动 `git commit`。计划里的 commit 步骤改成「请用户确认后再提交」。
- 计划完成后用 `ask_user` 问：在本会话按任务执行，还是先只保留计划。
- 没有 subagent 时，在本会话按任务推进，每任务结束停下来核对。
- 全程离线，不要拉取外网脚手架。

Announce at start: "I'm using the writing-plans skill to create the implementation plan."

## Scope Check

If the spec covers multiple independent subsystems, suggest separate
plans — one per subsystem. Each plan should produce working, testable
software on its own.

## File Structure

Before defining tasks, map out which files will be created or modified
and what each one is responsible for.

- Each file should have one clear responsibility.
- Prefer smaller, focused files.
- In existing codebases, follow established patterns.

## Task Right-Sizing

A task is the smallest unit that carries its own test cycle and is worth
a reviewer's gate. Fold setup into the task whose deliverable needs it.
Each task ends with an independently testable deliverable.

## Bite-Sized Task Granularity

Each step is one action (2-5 minutes):
- Write the failing test
- Run it to make sure it fails
- Implement the minimal code to make the test pass
- Run the tests and make sure they pass
- Stop for review (do not auto-commit)

## Plan Document Header

Every plan MUST start with this header:

```markdown
# [Feature Name] Implementation Plan

**Goal:** [One sentence]

**Architecture:** [2-3 sentences]

**Tech Stack:** [Key technologies]

**Spec:** [path to the spec this plan implements]

## Global Constraints

[Project-wide requirements copied from the spec — one line each]
```

## Task Structure

Each task lists:

- **Files:** create / modify / test paths
- **Interfaces:** consumes / produces (exact names and types)
- Checkbox steps with real code blocks, real commands, and expected output

## No Placeholders

Never write: TBD, TODO, "implement later", "add appropriate error
handling", "write tests for the above" without actual test code,
or "similar to Task N".

## Self-Review

After writing the plan, check it against the spec:

1. Spec coverage — every requirement maps to a task
2. Placeholder scan
3. Type consistency across tasks

## Execution Handoff

After saving the plan, ask via `ask_user`:

1. Execute tasks in this session, with a checkpoint after each task
2. Keep the plan only; implement later

Do not dispatch imaginary subagents if the runtime has none.
