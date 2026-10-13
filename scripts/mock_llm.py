#!/usr/bin/env python3
"""Offline OpenAI-compatible model for demos and end-to-end checks — no API key needed.

It recognises the 会话版 planner and member prompts and answers them with
deterministic, plausible replies so the whole group-chat pipeline (plan card,
parallel/pipeline steps, review, synthesis) can be exercised locally.

    python scripts/mock_llm.py --port 18931
    OPENAI_API_KEY=mock OPENAI_BASE_URL=http://127.0.0.1:18931/v1 OPENAI_MODEL=mock-team python main.py

This is a stand-in for local testing only; real use goes through the model
configured in 设置 → 模型.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="EdgeSidekick mock model")

PLANNER_MARK = "你是群聊的协作编排者"
MEMBER_MARK = "[社交群聊模式 · 团队协作]"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return str(content or "")


def _members(prompt: str) -> list[dict[str, str]]:
    block = prompt.split("群成员（名字（头衔）：职责）：", 1)[-1].split("\n\n", 1)[0]
    out = []
    for line in block.splitlines():
        m = re.match(r"^- (.+?)(?:（(.*?)）)?：(.*)$", line.strip())
        if m:
            out.append({"name": m.group(1), "title": m.group(2) or "", "duty": m.group(3)})
    return out


def _user_text(prompt: str) -> str:
    m = re.search(r"用户最新消息：\n「([\s\S]*?)」", prompt)
    return (m.group(1) if m else "").strip()


def _pick(members: list[dict[str, str]], *words: str) -> dict[str, str] | None:
    for w in words:
        for m in members:
            if w in m["name"] + m["title"] + m["duty"]:
                return m
    return None


def plan_for(prompt: str) -> dict[str, Any]:
    members = _members(prompt)
    text = _user_text(prompt)
    names = [m["name"] for m in members]
    if not members:
        return {"mode": "answer", "lead": "", "goal": text, "steps": [], "review": None, "synthesize": False, "reason": "no members"}
    lead = members[0]
    tester = _pick(members, "测试", "审查", "复核", "质量")
    dev = _pick(members, "实现", "程序", "开发", "代码") or lead
    arch = _pick(members, "架构", "设计", "方案", "产品")
    if not arch or arch["name"] == dev["name"]:
        arch = next((m for m in members if m["name"] not in (dev["name"], (tester or {}).get("name"))), lead)

    if re.search(r"^(你好|大家好|hi|hello|早上好|晚上好|谢谢|辛苦)", text, re.I):
        picks = members[: min(3, len(members))]
        return {
            "mode": "chat", "lead": picks[0]["name"], "goal": "回应问候",
            "steps": [{"id": f"s{i+1}", "member": m["name"], "task": "简短回应问候", "tools": False} for i, m in enumerate(picks)],
            "review": None, "synthesize": False, "reason": "寒暄，几位成员各回一句即可",
        }
    if re.search(r"讨论|怎么看|意见|利弊|对比|选哪个|头脑风暴", text):
        picks = members[: min(3, len(members))]
        return {
            "mode": "discuss", "lead": picks[0]["name"], "goal": f"就「{text[:40]}」给出多角度意见并形成结论",
            "steps": [
                {"id": f"s{i+1}", "member": m["name"], "task": f"从{m['title'] or '自己的职责'}角度评估", "expected": "2-3 条具体观点", "tools": False}
                for i, m in enumerate(picks)
            ],
            "review": None, "synthesize": len(picks) > 1, "reason": "需要不同视角权衡",
        }
    if re.search(r"分别|各自|同时|并行", text) and len(members) >= 2:
        picks = members[: min(3, len(members))]
        return {
            "mode": "parallel", "lead": picks[0]["name"], "goal": text[:60],
            "steps": [
                {"id": f"s{i+1}", "member": m["name"], "task": f"负责{m['title'] or m['name']}相关的部分", "expected": "要点清单", "tools": False}
                for i, m in enumerate(picks)
            ],
            "review": None, "synthesize": True, "reason": "子任务互不依赖，可以同时进行",
        }
    if re.search(r"实现|开发|写一个|做一个|设计.*(并|再|然后)|先.*再", text) and arch["name"] != dev["name"]:
        steps = [
            {"id": "s1", "member": arch["name"], "task": "拆解需求并给出接口与数据结构设计", "expected": "接口定义 + 关键约束", "tools": False},
            {"id": "s2", "member": dev["name"], "task": "按设计实现并自测", "expected": "可运行的代码与说明", "depends_on": ["s1"], "tools": True},
        ]
        review = {"member": tester["name"], "focus": "正确性、边界情况和错误处理"} if tester and tester["name"] not in (arch["name"], dev["name"]) else None
        return {"mode": "pipeline", "lead": arch["name"], "goal": text[:60], "steps": steps, "review": review, "synthesize": False, "reason": "先设计再实现，最后复核"}
    best = _pick(members, *(w for w in ("测试", "架构", "实现") if w in text)) or lead
    return {
        "mode": "answer", "lead": best["name"], "goal": text[:60],
        "steps": [{"id": "s1", "member": best["name"], "task": "回答用户的问题", "expected": "直接可用的答复", "tools": False}],
        "review": None, "synthesize": False, "reason": f"{best['name']}的职责最匹配，一人即可完成",
        "_names": names,
    }


def member_reply(prompt: str) -> str:
    who = (re.search(r"只输出「(.+?)」自己的话", prompt) or [None, "成员"])[1]
    task = ""
    for pat in (
        r"这是接力流水线中你负责的一步：(.+)",
        r"团队把任务拆开并行处理，你负责其中一部分：(.+)",
        r"这是多视角讨论。请从你的职责角度发表看法：(.+)",
        r"这件事由你一个人负责到底：(.+)",
        r"你是复核者。复核重点：(.+)",
    ):
        m = re.search(pat, prompt)
        if m:
            task = m.group(1).strip()
            break
    goal = (re.search(r"团队目标：(.+)", prompt) or [None, ""])[1].strip()
    if "用户在寒暄或客套" in prompt:
        return f"你好！我是{who}，有需要随时叫我。"
    if "你是复核者" in prompt:
        return f"APPROVE\n已对照需求核对接口、主流程和异常分支，{task.split('、')[0] if task else '关键点'}没有发现问题。"
    if "复核者对交付提出了问题" in prompt:
        return "已按复核意见逐条修正：补充了参数校验和失败时的错误返回。"
    if "你负责把团队的成果整合" in prompt:
        return (
            f"**结论**：围绕「{goal or '本次目标'}」，团队意见基本一致。\n\n"
            "- 先落地最小可用版本，再按反馈迭代\n"
            "- 风险点：边界输入与权限，需要在上线前补测试\n\n"
            "需要你决定：是否现在就开始第一阶段？"
        )
    if "这是多视角讨论" in prompt:
        return f"从{who}的角度看：{task or '这个问题'}最关键的是先明确约束条件；我建议先做小范围验证，再决定是否全面推广。"
    if "这是接力流水线中你负责的一步" in prompt:
        if "上游交付" in prompt:
            return (
                f"已按上游设计完成：{task}。\n\n```python\ndef login(user: str, password: str) -> dict:\n"
                "    if not user or not password:\n        raise ValueError(\"用户名和密码不能为空\")\n"
                "    return {\"token\": \"…\", \"user\": user}\n```\n自测：正常登录与空参数两种情况都已覆盖。"
            )
        return f"设计如下（{task}）：\n\n- `POST /api/login`，请求体 `{{user, password}}`\n- 成功返回 `{{token, user}}`，失败返回 401\n- 约束：密码不落日志，连续失败 5 次锁定 10 分钟"
    if "团队把任务拆开并行处理" in prompt:
        return f"{who}负责的部分（{task}）：\n- 要点一：先确认范围和验收标准\n- 要点二：列出依赖和风险\n- 要点三：给出可执行的下一步"
    if "这件事由你一个人负责到底" in prompt:
        duty = (re.search(r"职责: (.+)", prompt) or [None, ""])[1].strip()
        if re.search(r"能做什么|会什么|擅长", goal or task):
            return f"我是{who}。{duty or '我负责自己职责范围内的工作'}\n\n直接 @我 并说明目标和验收标准，我会把结果和验证方式一起交回来。"
        return f"好的，我来处理：{goal or task}。\n\n结论：先确认需求细节，再动手实现；完成后我会附上验证结果。"
    return f"{who}：收到。"


def reply_for(messages: list[dict[str, Any]]) -> str:
    last_user = next((_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
    if PLANNER_MARK in last_user:
        plan = plan_for(last_user)
        plan.pop("_names", None)
        return json.dumps(plan, ensure_ascii=False)
    if MEMBER_MARK in last_user:
        return member_reply(last_user)
    snippet = re.sub(r"\s+", " ", last_user).strip()[:60]
    return f"（离线演示模型）收到：{snippet or '你的消息'}。在「设置 → 模型」配置真实模型后即可获得完整回答。"


def _chunk(cid: str, model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    body = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "mock-team", "object": "model", "owned_by": "local"}]}


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    model = str(body.get("model") or "mock-team")
    text = reply_for(body.get("messages") or [])
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    if not body.get("stream"):
        return JSONResponse(
            {
                "id": cid,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": len(text), "total_tokens": len(text)},
            }
        )

    async def gen():
        import asyncio

        yield _chunk(cid, model, {"role": "assistant", "content": ""})
        step = 6
        for i in range(0, len(text), step):
            yield _chunk(cid, model, {"content": text[i : i + step]})
            await asyncio.sleep(0.015)
        yield _chunk(cid, model, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18931)
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
