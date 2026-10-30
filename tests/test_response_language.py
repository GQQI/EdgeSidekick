"""Reply language follows the user without rewriting the tool protocol."""

from metateam.runtime.prompts import (
    CORE,
    apply_response_language,
    language_pin_messages,
    user_language,
)


def test_user_language_follows_the_turn() -> None:
    assert user_language("把登录页改成可注册") == "zh"
    assert user_language("Add a signup button to the login page") == "en"


def test_response_language_pins_both_ends_without_rewriting_tools() -> None:
    base = "You are EdgeSidekick.\n\n# Tools\nCall read_file with JSON arguments."
    zh = apply_response_language(base, "把登录页改成可注册")
    en = apply_response_language(zh, "Add a signup button")
    assert zh.startswith("# Reply language\n")
    assert "简体中文" in zh
    assert "# Tools\nCall read_file" in zh
    assert en.count("# Reply language\n") == 1
    assert "Do not think or answer in Chinese." in en
    assert "简体中文" not in en.split("# Tools")[0]
    assert zh.split("# Response language")[0].split("\n\n", 1)[1] == en.split("# Response language")[0].split("\n\n", 1)[1]


def test_language_pin_is_a_copy_and_follows_the_latest_message() -> None:
    history = [
        {"role": "system", "content": "tools stay English"},
        {"role": "user", "content": "把登录页改成可注册"},
        {"role": "tool", "content": "ERROR: file missing"},
    ]
    pinned = language_pin_messages(history, "把登录页改成可注册")
    assert pinned is not history
    assert len(history) == 3
    assert pinned[-1]["role"] == "user"
    assert "简体中文" in pinned[-1]["content"]
    assert "not the end marker" in pinned[-1]["content"]

    again = language_pin_messages(pinned, "把登录页改成可注册")
    assert again[-1]["content"].count("Reply language for this call only.") == 1


def test_finish_is_decided_on_the_tool_result_reply() -> None:
    assert "After each tool result, decide in that same reply" in CORE
    assert "answer with no tool call" in CORE
