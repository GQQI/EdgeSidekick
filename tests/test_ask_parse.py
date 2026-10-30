from __future__ import annotations

from metateam.runtime.ask import should_keep_as_markdown, try_parse_inline_ask


def test_inline_ask_disabled() -> None:
    text = """请选择部署方式：
1. Docker
2. 本地安装
3. 只生成配置
"""
    assert try_parse_inline_ask(text) is None
    assert try_parse_inline_ask("下面是几个要点：\n1. a\n2. b") is None


def test_should_keep_keypoints_as_markdown() -> None:
    assert should_keep_as_markdown(
        "任意问题",
        [
            "这是一段超过一百八十字的报告体选项，用来说明选项本身已经是在陈述结论而不是让用户点选："
            + ("说明" * 100),
            "另一段同样很长的报告体选项，同样不是互斥选择按钮：" + ("补充" * 100),
        ],
    )
    assert should_keep_as_markdown(
        "请选择",
        ["**架构**：模块边界还是比较清晰", "**性能**：热点路径需要缓存"],
    )
    assert not should_keep_as_markdown("请选择部署方式", ["Docker", "本地安装"])


def test_long_exclusive_options_stay_a_decision() -> None:
    labels = [
        "全部删除：deps、public、app.db、package.json，以及本地 .config",
        "只删项目文件：deps、public、app.db、package.json，保留本地 .config",
        "只删遗留数据：public 和 app.db，保留 deps、package.json 和本地配置",
    ]
    assert not should_keep_as_markdown("要删除哪一类文件？", labels)


def test_confirm_with_structured_options_is_a_decision() -> None:
    labels = [
        "方案 A：组件库甲 / 服务端甲 / 令牌认证",
        "方案 B：组件库乙 / 服务端乙 / 会话认证",
        "方案 C：轻量页 / 服务端丙 / 会话认证",
        "方案 D：组件库丁 / 服务端甲 / 令牌认证",
    ]
    assert not should_keep_as_markdown("请确认以下方案组合：", labels)
