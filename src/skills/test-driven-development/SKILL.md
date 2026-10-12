---
name: test-driven-development
description: >
  Write a failing test before implementation code. Use when implementing
  a feature or bugfix in a codebase that has (or should have) automated
  tests. Do not use for throwaway prototypes, generated files, or
  document / slide / spreadsheet deliverables.
license: Complete terms in LICENSE.txt
---

# Test-Driven Development (TDD)

## Overview

Write the test first. Watch it fail. Write minimal code to pass.

**Core principle:** If you didn't watch the test fail, you don't know if it tests the right thing.

## EdgeSidekick / 离线适配

- 用项目里已有的测试命令（pytest / npm test 等），不要为此安装新框架。
- 参考 `writing-good-tests.md`（本目录）。
- 不要自动 commit。
- 宣称完成前走 `verification-before-completion`。

## The Iron Law

```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

Write code before the test? Delete it. Start over. Don't keep it as
"reference". Don't adapt it while writing tests.

## When to Use

Always for new features, bug fixes, refactoring, and behavior changes.

Exceptions (ask the user): throwaway prototypes, generated code, configuration files.

## Red-Green-Refactor

### RED — Write Failing Test

Write one minimal test showing what should happen.

Good: clear name, tests real behavior, one thing.

Bad: vague name, tests a mock instead of the code.

Requirements: one behavior, clear name, real code (no mocks unless unavoidable).

### Verify RED — Watch It Fail

MANDATORY. Never skip.

Confirm:
- Test fails (not errors)
- Failure message is expected
- Fails because the feature is missing (not typos)

Test passes? You're testing existing behavior. Fix the test.
Test errors? Fix the error, re-run until it fails correctly.

### GREEN — Minimal Code

Write the simplest code to pass the test. Don't add features, refactor
other code, or "improve" beyond the test.

### Verify GREEN — Watch It Pass

Confirm the new test passes, other tests still pass, output is clean.
Test fails? Fix code, not the test.

### REFACTOR — Clean Up

After green only: remove duplication, improve names, extract helpers.
Keep tests green. Don't add behavior.

## Good Tests

| Quality | Good | Bad |
|---------|------|-----|
| Minimal | One thing. "and" in the name? Split. | `test('validates email and domain and whitespace')` |
| Clear | Name describes behavior | `test('test1')` |
| Shows intent | Demonstrates desired API | Obscures what code should do |

When writing or changing any test, read `writing-good-tests.md`.

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| Too simple to test | Simple code breaks. Test takes 30 seconds. |
| I'll test after | Tests written after pass immediately and prove nothing. |
| Already manually tested | No record, no re-run, easy to forget cases. |
| Need to explore first | Fine. Throw away exploration, start with TDD. |
| TDD will slow me down | Catching bugs before commit is faster than production debugging. |

## Red Flags — STOP and Start Over

- Code before test
- Test after implementation
- Test passes immediately
- Can't explain why the test failed
- "Just this once"

## Verification Checklist

- [ ] Every new function/method has a test
- [ ] Watched each test fail before implementing
- [ ] Each test failed for the expected reason
- [ ] Wrote minimal code to pass each test
- [ ] All tests pass
- [ ] Tests use real code (mocks only if unavoidable)
- [ ] Edge cases and errors covered
