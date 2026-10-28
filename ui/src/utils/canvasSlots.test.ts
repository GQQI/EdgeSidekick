import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  alignCanvasSlots,
  canvasPlaceholderId,
  limitCanvasRoots,
  peakCanvasSlotCount,
} from "./canvasSlots.ts";
import {
  declaredDelegateSlotCount,
  expandParallelCanvasRoots,
  nestHelperAgents,
  sameSubagentSlot,
  unionCanvasNodes,
} from "./chatHelpers.ts";
import type { ChatMsg, SubNode } from "../types/chat";

function task(id: string, goal: string): SubNode {
  return { id, goal, role: "leaf", kind: "task", status: "running", transcript: [] };
}

describe("canvas slot stability", () => {
  it("does not merge distinct pending placeholders by goal", () => {
    const a = task(canvasPlaceholderId("pending", "c1", 0), "调查 A");
    const b = task(canvasPlaceholderId("pending", "c1", 1), "调查 A");
    assert.equal(sameSubagentSlot(a, b), false);
  });

  it("binds a real child onto a pending slot by goal", () => {
    const pending = task(canvasPlaceholderId("pending", "c1", 0), "写报告");
    const real = task("child-9", "写报告");
    assert.equal(sameSubagentSlot(pending, real), true);
  });

  it("alignCanvasSlots never grows past the primary slot count", () => {
    const primary = [
      task(canvasPlaceholderId("pending", "c1", 0), "任务一"),
      task(canvasPlaceholderId("pending", "c1", 1), "任务二"),
      task(canvasPlaceholderId("pending", "c1", 2), "任务三"),
    ];
    const extras = [
      task("r1", "任务一"),
      task("r2", "任务二"),
      task("r3", "任务三"),
      task("r4", "多余编排"),
    ];
    const aligned = alignCanvasSlots(primary, extras);
    assert.equal(aligned.length, 3);
    assert.deepEqual(
      aligned.map((n) => n.id),
      ["r1", "r2", "r3"],
    );
  });

  it("unionCanvasNodes keeps the larger side when tool replay is short", () => {
    const tree = [task("a", "一"), task("b", "二"), task("c", "三")];
    const tools = [task("a", "一"), task("b", "二")];
    const merged = unionCanvasNodes(tree, tools, tools.length);
    assert.equal(merged.length, 3);
  });

  it("declaredDelegateSlotCount freezes at seeded peak during partial tool args", () => {
    const stage = 1;
    const messages: ChatMsg[] = [
      {
        id: "t1",
        role: "tool",
        content: "",
        stage,
        tool: {
          id: "t1",
          callId: "c1",
          name: "delegate_task",
          args: { tasks: [{ goal: "仅一个" }] },
          status: "streaming",
          summary: "",
        },
      },
      {
        id: "s0",
        role: "subagent",
        content: "",
        stage,
        subagent: task(canvasPlaceholderId("pending", "c1", 0), "一"),
      },
      {
        id: "s1",
        role: "subagent",
        content: "",
        stage,
        subagent: task(canvasPlaceholderId("pending", "c1", 1), "二"),
      },
      {
        id: "s2",
        role: "subagent",
        content: "",
        stage,
        subagent: task(canvasPlaceholderId("pending", "c1", 2), "三"),
      },
    ];
    assert.equal(declaredDelegateSlotCount(messages, stage), 3);
    assert.equal(peakCanvasSlotCount(messages.map((m) => m.subagent!).filter(Boolean)), 3);
  });

  it("limitCanvasRoots prefers real ids then trims to declared", () => {
    const nodes = [
      task("r1", "一"),
      task(canvasPlaceholderId("pending", "c1", 1), "二"),
      task("r3", "三"),
      task("r4", "四"),
    ];
    const limited = limitCanvasRoots(nodes, 3);
    assert.equal(limited.length, 3);
    assert.ok(limited.every((n) => n.id !== "r4") || limited.length === 3);
  });

  it("expands an empty-goal wrapper into peer workers only", () => {
    const wrapped: SubNode = {
      ...task("lead", ""),
      goal: "",
      children: [task("a", "一"), task("b", "二"), task("c", "三")],
    };
    const peers = expandParallelCanvasRoots([wrapped]);
    assert.equal(peers.length, 3);
    assert.deepEqual(
      peers.map((n) => n.id),
      ["a", "b", "c"],
    );
  });

  it("keeps a finished worker visible with its nested siblings", () => {
    const wrapped: SubNode = {
      ...task("done-1", "已完成一"),
      status: "done",
      children: [task("run-2", "进行中二"), task("run-3", "进行中三")],
    };
    const peers = expandParallelCanvasRoots([wrapped]);
    assert.equal(peers.length, 3);
    assert.deepEqual(
      peers.map((n) => n.id),
      ["done-1", "run-2", "run-3"],
    );
  });

  it("does not nest sibling task workers under each other via parent_id", () => {
    const nodes = [
      task("a", "一"),
      { ...task("b", "二"), parent_id: "a" },
      { ...task("c", "三"), parent_id: "a" },
    ];
    const rooted = nestHelperAgents(nodes);
    assert.equal(rooted.length, 3);
  });
});
