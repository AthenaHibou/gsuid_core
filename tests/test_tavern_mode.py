"""酒馆模式（M0）的结构与行为测试。

覆盖：
1. `mes_example` -> 原生 user/assistant 消息（宏替换、多行、开头/结尾归一）；
2. 私聊 tavern bundle vs 群聊 gscore 回退；
3. tavern system prompt 含卡片/工具协议、不含群聊锚点；
4. user payload 在 tavern 下只留正文；
5. 输出档位保留旁白/段落、不拆 IM 气泡；
6. pydantic-ai 下“示例历史 + 工具调用”可共存（机制级，不调真实模型）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from gsuid_core.ai_core.persona import tavern as tavern_mod
from gsuid_core.ai_core.persona import persona as persona_mod
from gsuid_core.ai_core.persona.tavern import (
    build_example_messages,
    build_tavern_bundle,
    load_tavern_config,
)
from gsuid_core.ai_core.utils import (
    prepare_content_payload,
    reset_output_single_message,
    reset_output_style,
    send_chat_result,
    set_output_single_message,
    set_output_style,
)
from gsuid_core.models import Event


def _event(**overrides) -> Event:
    fields = {
        "bot_id": "onebot",
        "bot_self_id": "1",
        "msg_id": "m",
        "user_type": "private",
        "user_id": "123",
        "text": "在吗",
        "sender": {"nickname": "博士"},
    }
    fields.update(overrides)
    return Event(**fields)


def _write_persona(tmp_path: Path, name: str = "测试角色", *, scope: str = "private") -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "persona.md").write_text("# 测试角色\n\n性格冷淡，说话很短。", encoding="utf-8")
    (d / "tavern.json").write_text(
        json.dumps(
            {
                "name": "测试角色",
                "main_prompt": "写{{char}}的下一条回复，只写角色的话。",
                "mes_example": (
                    "<START>\n"
                    "{{user}}: 在吗\n"
                    "{{char}}: 在。\n"
                    "<START>\n"
                    "{{user}}: 今天好累\n"
                    "{{char}}: 那就歇着。\n"
                    "{{char}}: 别硬撑。\n"
                ),
                "post_history_instructions": "保持短句，不要客服腔。",
                "author_note": {"text": "不要解释自己的思路。", "depth": 0},
                "scope": scope,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def persona_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tavern_mod, "PERSONA_PATH", tmp_path)
    monkeypatch.setattr(persona_mod, "PERSONA_PATH", tmp_path)
    _write_persona(tmp_path)
    return tmp_path


def test_example_parser_builds_alternating_messages(persona_root: Path) -> None:
    text = (
        "<START>\n{{user}}: 在吗\n{{char}}: 在。\n"
        "<START>\n{{user}}: 今天好累\n{{char}}: 那就歇着。\n{{char}}: 别硬撑。\n"
    )
    messages = build_example_messages(text, "白面鸮", "你")
    assert len(messages) == 4
    assert isinstance(messages[0], ModelRequest)
    assert isinstance(messages[0].parts[0], UserPromptPart)
    assert messages[0].parts[0].content == "在吗"
    assert isinstance(messages[1], ModelResponse)
    assert isinstance(messages[1].parts[0], TextPart)
    assert messages[1].parts[0].content == "在。"
    # 连续两条 char 合并成一条 assistant，保持 user/assistant 交替
    assert messages[3].parts[0].content == "那就歇着。\n别硬撑。"


def test_example_parser_replaces_macros_and_keeps_multiline() -> None:
    messages = build_example_messages(
        "<START>\n{{user}}: 你好\n{{char}}: 第一行\n第二行\n",
        "白面鸮",
        "博士",
    )
    assert messages[0].parts[0].content == "你好"
    assert messages[1].parts[0].content == "第一行\n第二行"


def test_tavern_config_defaults_scope_private(persona_root: Path) -> None:
    cfg = load_tavern_config("测试角色")
    assert cfg.scope == "private"
    assert cfg.output_style == "roleplay"
    assert cfg.single_message is True


def test_tavern_bundle_private_builds_card_examples_phi(persona_root: Path) -> None:
    bundle = asyncio.run(build_tavern_bundle(_event(), "测试角色"))
    assert bundle is not None
    assert bundle.profile == "tavern"
    assert bundle.input_style == "roleplay"
    assert bundle.output_style == "roleplay"
    assert bundle.single_message is True
    assert "## 角色设定" in bundle.system_prompt
    assert "性格冷淡" in bundle.system_prompt
    assert "戏外能力" in bundle.system_prompt
    assert "web_search_tool" in bundle.system_prompt
    assert "必须先调用工具" in bundle.system_prompt
    assert "正在群里打字聊天" not in bundle.system_prompt
    assert len(bundle.example_messages) == 4
    assert "保持短句" in bundle.post_history
    assert "不要解释自己的思路" in bundle.post_history


def test_session_bundle_selects_tavern_profile(
    persona_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import gsuid_core.ai_core.context_assembly as context_assembly
    import gsuid_core.ai_core.persona.config as persona_config

    class _FakeCfg:
        def get_config(self, key: str) -> SimpleNamespace:
            return SimpleNamespace(data="tavern" if key == "prompt_profile" else None)

    monkeypatch.setattr(persona_config.persona_config_manager, "get_config", lambda name: _FakeCfg())
    bundle = asyncio.run(context_assembly.build_session_prompt_bundle(_event(), "测试角色"))
    assert bundle.profile == "tavern"
    assert bundle.system_prompt
    assert bundle.example_messages


def test_tavern_payload_strips_group_meta(persona_root: Path) -> None:
    ev = _event()
    gscore = asyncio.run(prepare_content_payload(ev, profile="gscore"))
    tavern = asyncio.run(prepare_content_payload(ev, profile="tavern"))
    assert "--- 消息 ---" in gscore[0]
    assert "博士" in gscore[0]
    assert tavern[0] == "在吗"


def test_roleplay_output_keeps_narration_and_single_bubble(persona_root: Path) -> None:
    class _FakeBot:
        def __init__(self) -> None:
            self.sent: list = []

        async def send(self, seg, **kwargs) -> None:
            self.sent.append(seg)

    bot = _FakeBot()
    ev = _event()
    style_token = set_output_style("roleplay")
    single_token = set_output_single_message(True)
    try:
        asyncio.run(send_chat_result(bot, "*揉了揉眼睛*\n\n（她沉默了一会儿）\n\n在。", ev=ev))
    finally:
        reset_output_single_message(single_token)
        reset_output_style(style_token)

    assert len(bot.sent) == 1
    text = bot.sent[0][0].data
    assert "揉了揉眼睛" in text
    assert "（她沉默了一会儿）" in text
    assert "*" not in text


def test_tavern_api_read_write_delete(persona_root: Path) -> None:
    from gsuid_core.webconsole.persona_api import (
        delete_persona_tavern,
        get_persona_tavern,
        update_persona_tavern,
    )

    (persona_root / "测试角色" / "tavern.json").unlink()
    before = asyncio.run(get_persona_tavern("测试角色", {}))
    assert before["status"] == 0
    assert before["data"]["exists"] is False

    written = asyncio.run(
        update_persona_tavern("测试角色", {"config": {"main_prompt": "x", "scope": "private"}}, {})
    )
    assert written["status"] == 0
    after = asyncio.run(get_persona_tavern("测试角色", {}))
    assert after["data"]["exists"] is True
    assert after["data"]["config"]["main_prompt"] == "x"

    removed = asyncio.run(delete_persona_tavern("测试角色", {}))
    assert removed["status"] == 0
    final = asyncio.run(get_persona_tavern("测试角色", {}))
    assert final["data"]["exists"] is False


def test_engine_resolution_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gsuid_core.ai_core.persona.engines as engines

    class _Cfg:
        def __init__(self, data):
            self.data = data

    class _AI:
        def __init__(self, *, enable=True, tavern=True, default="gscore"):
            self.values = {
                "enable": enable,
                "engine_tavern_enable": tavern,
                "engine_default": default,
            }

        def get_config(self, key):
            return _Cfg(self.values.get(key))

    class _PersonaCfg:
        def __init__(self, profile):
            self.profile = profile

        def get_config(self, key):
            assert key == "prompt_profile"
            return _Cfg(self.profile)

    monkeypatch.setattr(engines, "PERSONA_PATH", tmp_path)
    (tmp_path / "测试角色").mkdir()
    monkeypatch.setattr(engines, "ai_config", _AI())
    monkeypatch.setattr(
        engines.persona_config_manager,
        "get_config",
        lambda name: _PersonaCfg("gscore"),
    )

    engines.save_engine_config(
        "测试角色",
        engines.EngineConfig(
            default="gscore",
            overrides={"group:456": "tavern", "user:123": "gscore"},
        ),
    )
    group_ev = _event(user_type="group", group_id="456")
    private_ev = _event()
    assert asyncio.run(engines.resolve_engine(group_ev, "测试角色")) == "tavern"
    assert asyncio.run(engines.resolve_engine(private_ev, "测试角色")) == "gscore"

    # 人格默认：engines.json default 为空时回退 prompt_profile
    engines.save_engine_config("测试角色", engines.EngineConfig(default="", overrides={}))
    monkeypatch.setattr(
        engines.persona_config_manager,
        "get_config",
        lambda name: _PersonaCfg("tavern"),
    )
    assert asyncio.run(engines.resolve_engine(private_ev, "测试角色")) == "tavern"

    # 全局默认：人格默认也为空时回退 engine_default
    monkeypatch.setattr(engines, "ai_config", _AI(default="tavern"))
    monkeypatch.setattr(
        engines.persona_config_manager,
        "get_config",
        lambda name: _PersonaCfg(""),
    )
    assert asyncio.run(engines.resolve_engine(private_ev, "测试角色")) == "tavern"

    # 酒馆总开关关闭：tavern 全部回退 core
    monkeypatch.setattr(engines, "ai_config", _AI(tavern=False, default="tavern"))
    assert asyncio.run(engines.resolve_engine(private_ev, "测试角色")) == "gscore"

    # AI 总开关关闭：两个引擎都不走
    monkeypatch.setattr(engines, "ai_config", _AI(enable=False, default="tavern"))
    assert asyncio.run(engines.resolve_engine(private_ev, "测试角色")) == "gscore"


def test_examples_and_tool_call_coexist_in_pydantic_ai() -> None:
    seen: list[list[tuple[str, list[str]]]] = []

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        seen.append([(type(m).__name__, [type(p).__name__ for p in m.parts]) for m in messages])
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("get_weather", {"city": "上海"})])
        return ModelResponse(parts=[TextPart("明天有雨。")])

    agent = Agent(FunctionModel(model_fn), system_prompt="MAIN")

    @agent.tool_plain
    def get_weather(city: str) -> str:
        """查天气"""
        return "雨"

    examples = build_example_messages("<START>\n{{user}}: 在吗\n{{char}}: 在。\n", "白面鸮", "你")
    result = asyncio.run(agent.run("明天天气", message_history=examples))
    assert result.output == "明天有雨。"
    assert len(seen) >= 2
    # 第一次请求里已含示例消息；第二次请求里已含工具调用/返回
    assert [name for name, _ in seen[0]] == ["ModelRequest", "ModelResponse", "ModelRequest"]
    assert any("ToolCallPart" == part for call in seen for _, parts in call for part in parts)
