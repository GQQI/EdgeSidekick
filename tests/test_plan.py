from __future__ import annotations

from metateam.runtime.plan import (
    _PLAN_SYSTEM,
    coalesce_layered_writes,
    parse_goal_ready_reply,
    parse_needs_plan_reply,
)


def test_parse_goal_ready_json() -> None:
    assert parse_goal_ready_reply('{"ready": true, "reason": "named file"}') is True
    assert parse_goal_ready_reply('{"ready": false, "reason": "no object"}') is False
    assert parse_goal_ready_reply("not json") is None
    assert parse_goal_ready_reply("") is None


def test_parse_goal_ready_fenced() -> None:
    raw = '```json\n{"ready": false, "reason": "fragment"}\n```'
    assert parse_goal_ready_reply(raw) is False


def test_parse_needs_plan_still_works() -> None:
    assert parse_needs_plan_reply('{"plan": false}') is False
    assert parse_needs_plan_reply('{"plan": true, "reason": "refactor"}') is True


def test_plan_prompt_writes_a_new_file_once_then_verifies() -> None:
    assert "skeleton step" in _PLAN_SYSTEM
    assert "one verification task" in _PLAN_SYSTEM


def test_single_new_file_layers_collapse_to_one_write_then_verify() -> None:
    tasks = [
        {
            "id": "1",
            "title": "搭建 app.html 骨架并跑通渲染循环",
            "detail": "写入骨架。CDN 示例 three@0.160.0/build/three.module.js。本步不生成玩法。",
            "status": "pending",
        },
        {
            "id": "2",
            "title": "实现世界数据",
            "detail": "在同一文件里加地形与网格。",
            "status": "pending",
        },
        {
            "id": "3",
            "title": "实现操作与存档",
            "detail": "补上键鼠和本地存档。",
            "status": "pending",
        },
        {
            "id": "4",
            "title": "浏览器验证",
            "detail": "打开页面，确认能玩。",
            "status": "pending",
        },
    ]
    shape = {
        "reuse": "工作区无既有代码，全部落在 E:/work/app.html",
        "create_only_if": "只允许新建 app.html 一个文件",
        "why_not_smaller": "分层可验证，合并会反复重写",
    }
    out = coalesce_layered_writes(tasks, shape)
    assert len(out) == 2
    assert out[0]["title"] == "写完 app.html"
    assert "地形" in out[0]["detail"]
    assert "存档" in out[0]["detail"]
    assert "one write_file" in out[0]["detail"]
    assert out[1]["title"] == "浏览器验证"


def test_distinct_files_stay_separate_and_same_file_steps_merge() -> None:
    tasks = [
        {"id": "1", "title": "创建 server.js", "detail": "Express API", "status": "pending"},
        {"id": "2", "title": "创建 public/index.html", "detail": "入口页面", "status": "pending"},
        {
            "id": "3",
            "title": "实现 public/js/game.js 引擎",
            "detail": "循环和碰撞",
            "status": "pending",
        },
        {"id": "4", "title": "在 game.js 里补关卡", "detail": "地图和敌人", "status": "pending"},
        {"id": "5", "title": "测试游戏流程", "detail": "启动服务器并在浏览器试玩", "status": "pending"},
    ]
    shape = {"reuse": "none yet", "create_only_if": "所有文件全新创建"}
    out = coalesce_layered_writes(tasks, shape)
    assert len(out) == 4
    assert "server.js" in out[0]["title"]
    assert "index.html" in out[1]["title"]
    assert out[2]["title"] == "写完 game.js"
    assert "碰撞" in out[2]["detail"] and "敌人" in out[2]["detail"]
    assert out[3]["title"] == "测试游戏流程"

