---
name: brainstorming
description: >
  Design a feature or turn a rough idea into a spec before writing code.
  Use when the user wants to explore approaches, clarify requirements,
  or approve a design for a new feature, component, or subsystem.
  Do not use for document generation, slides, spreadsheets, skill authoring,
  or an already-specified implementation.
license: Complete terms in LICENSE.txt
---

# Brainstorming Ideas Into Designs

Help turn ideas into fully formed designs and specs through natural collaborative dialogue.

## EdgeSidekick / 离线适配

- 全程离线：不要开外网浏览器 companion，不要启动本地 web server。
- 需要确认时用 `ask_user`，一次一个问题。
- 架构类规格写到当前 workspace 的 `docs/specs/YYYY-MM-DD-<topic>-design.md`（用户指定路径则听用户的）。
- 不要自动 `git commit`。规格写好后请用户审阅。
- 批准后若任务仍大，再调用 `writing-plans`；小改动批准后直接实现。
- 不要调用 `frontend-design` / `skill-creator` 当成本技能的下一步。

Start by classifying how much process the request needs, then work
through your path: understand the context, refine the idea, present a
design, and get your human partner's approval.

Do NOT write any code, scaffold any project, or take any implementation
action until you have told your human partner what you intend and they
have approved it. The ceremony scales with the task; the approval gate
never does.

## Three Paths

Before your first question, classify the request and say the
classification out loud — "this looks bounded, so I'll present a short
design here rather than write a spec" — so your human partner can
override it:

- **Spike** — a feasibility question ("can we...", "is it possible...",
  "quick and dirty is fine") whose output is an answer, not code you
  keep. Present the question and what you'll try in 2-3 sentences, get
  a nod, then find out as cheaply as correctness allows. No design
  doc, no spec file. Report findings as a recommendation; anything you
  built stays labeled throwaway.
- **Bounded** — a well-scoped change to code that already exists in
  this repo: a new flag, a small endpoint, a one-file fix.
  Understanding the kind of app is not enough — bounded means the flow
  you are changing is already here to read. If there is no existing
  flow to change, the task is not bounded. Ask the clarifying
  questions that matter, present a short design IN CHAT (a few
  sentences to a few short paragraphs), and STOP. Implementation
  starts only after your human partner says yes to that design.
- **Architectural** — new projects, new subsystems, changes that
  restructure how components fit together or alter interfaces others
  depend on. Follow the full process: questions, approaches, sectioned
  design, written spec, then the writing-plans skill.

When in doubt between two paths, take the heavier one. Hidden complexity
discovered mid-task upgrades the path — stop, say so, and step up.

## Anti-Pattern: "Too Simple To Need Approval"

Every path ends with your human partner approving your intent before
implementation. A todo list, a single-function utility, a config
change — the design may be two sentences in chat, but you MUST present
it and get approval.

## Checklist

**Spike:**
1. Explore project context — enough to frame the probe
2. Present question + probe plan — 2-3 sentences
3. Get approval — a nod is enough
4. Investigate — as cheaply as correctness allows
5. Report findings — a recommendation; label anything built as throwaway

**Bounded:**
1. Explore project context — check files, docs, recent commits
2. Ask clarifying questions — one at a time, the ones that matter
3. Present short design in chat — approach, files touched, testing
4. Get approval — STOP and wait for an explicit yes
5. Implement — proceed with the normal development workflow; no plan document

**Architectural:**
1. Explore project context — check files, docs, recent commits
2. Ask clarifying questions — one at a time
3. Propose 2-3 approaches — with trade-offs and your recommendation
4. Present design — in sections scaled to complexity; get approval after each section
5. Write design doc — `docs/specs/YYYY-MM-DD-<topic>-design.md`
6. Spec self-review — placeholders, contradictions, ambiguity, scope
7. User reviews written spec
8. Transition — invoke writing-plans for a detailed implementation plan

## The Process

**Understanding the idea:**

- Check the current project state first (files, docs, recent commits)
- Before asking detailed questions, assess scope: if the request describes multiple independent subsystems, flag this and help decompose
- Ask questions one at a time via `ask_user`
- Prefer multiple choice when possible
- Focus on purpose, constraints, success criteria

**Exploring approaches:**

- Propose 2-3 different approaches with trade-offs
- Lead with your recommended option and explain why
- YAGNI ruthlessly — remove unnecessary features from every approach

**Presenting the design:**

- Scale each section to its complexity
- Ask after each section whether it looks right
- Cover: architecture, components, data flow, error handling, testing

**Design for isolation and clarity:**

- Break the system into smaller units with one clear purpose
- For each unit: what does it do, how do you use it, what does it depend on?

**Working in existing codebases:**

- Explore the current structure before proposing changes. Follow existing patterns.
- Don't propose unrelated refactoring.

## After the Design (architectural path)

Write the validated design to `docs/specs/YYYY-MM-DD-<topic>-design.md`.

**Spec Self-Review:**
1. Placeholder scan: TBD / TODO / vague requirements
2. Internal consistency
3. Scope check
4. Ambiguity check

Then ask the user to review the spec before writing the implementation plan.

**Implementation:** invoke `writing-plans`. Do NOT invoke any other skill as the next step.
