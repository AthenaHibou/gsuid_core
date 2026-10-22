"""拟人化两个护栏的回归测试（2026-10-21）：

1. **工具前摇**：``_resolve_pre_tool_expression`` 的人格表优先 / 框架默认兜底 /
   列表随机 / 空串不前摇；且发送点**不受** ``suppress_intermediate_text`` 影响
   （历史上正是被它压掉，导致长工具全程静默、体感像掉线）。
2. **客服腔打回**：``check_service_tone`` 只认强特征固定句式，日常礼貌语不误杀；
   ``pre_send_gate`` 一轮只打回一次，第二次放行，避免改写循环。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from gsuid_core.ai_core import output_gate, output_firewall as of
from gsuid_core.ai_core.gs_agent import GsCoreAIAgent, _resolve_pre_tool_expression
from gsuid_core.ai_core.output_gate import GateBag, GateDecision, pre_send_gate

_ROOT = Path(__file__).resolve().parent.parent


# ── 一、工具前摇 ────────────────────────────────────────────────────


class _FakeCfg:
    def __init__(self, data):
        self.data = data


class _FakePersonaCfg:
    def __init__(self, data):
        self._data = data

    def get_config(self, key):
        assert key == "pre_tool_expressions"
        return _FakeCfg(self._data)


def test_persona_table_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    import gsuid_core.ai_core.persona.config as pc

    monkeypatch.setattr(
        pc.persona_config_manager,
        "get_config",
        lambda name: _FakePersonaCfg({"web_search_tool": ["[正在连接至罗德岛中央网络]"]}),
    )
    assert _resolve_pre_tool_expression("白面鸮", "web_search_tool") == "[正在连接至罗德岛中央网络]"


def test_persona_string_value_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    """兼容旧手写格式（工具→裸字符串）；配置层会把它规整成数组，但读取端两种都认。"""
    import gsuid_core.ai_core.persona.config as pc

    monkeypatch.setattr(pc.persona_config_manager, "get_config", lambda name: _FakePersonaCfg({"web_fetch_tool": "看看"}))
    assert _resolve_pre_tool_expression("白面鸮", "web_fetch_tool") == "看看"


def test_framework_fallback_when_persona_lacks_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    import gsuid_core.ai_core.persona.config as pc

    monkeypatch.setattr(pc.persona_config_manager, "get_config", lambda name: _FakePersonaCfg({}))
    # 人格表里没有该工具 → 用框架中性台词，保证任何人格都不静默
    assert _resolve_pre_tool_expression("白面鸮", "web_search_tool") == "稍等，我查一下相关信息…"


def test_empty_string_disables_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    import gsuid_core.ai_core.persona.config as pc

    monkeypatch.setattr(pc.persona_config_manager, "get_config", lambda name: _FakePersonaCfg({"web_fetch_tool": "  "}))
    assert _resolve_pre_tool_expression("白面鸮", "web_fetch_tool") is None


def test_list_value_picks_one(monkeypatch: pytest.MonkeyPatch) -> None:
    import gsuid_core.ai_core.persona.config as pc

    monkeypatch.setattr(
        pc.persona_config_manager,
        "get_config",
        lambda name: _FakePersonaCfg({"render_html_to_image": ["[制图模块已调用]", "[正在出图]"]}),
    )
    got = _resolve_pre_tool_expression("白面鸮", "render_html_to_image")
    assert got in ("[制图模块已调用]", "[正在出图]")


def test_unknown_tool_has_no_expression(monkeypatch: pytest.MonkeyPatch) -> None:
    import gsuid_core.ai_core.persona.config as pc

    monkeypatch.setattr(pc.persona_config_manager, "get_config", lambda name: _FakePersonaCfg({}))
    assert _resolve_pre_tool_expression("白面鸮", "add_once_task") is None


def test_pre_tool_send_is_not_gated_by_suppress_intermediate_text() -> None:
    """源码级锁：前摇发送点不能挂在 ``_suppress_intermediate_text`` 条件里。

    历史事故：早期实现把前摇放在 ``not suppress_intermediate_text`` 分支下，
    而该配置默认 True → 前摇永远不发，长工具全程静默。
    """
    src = inspect.getsource(GsCoreAIAgent._execute_run_once)
    assert "_maybe_send_pre_tool_expression" in src
    call_line = next(ln for ln in src.splitlines() if "_maybe_send_pre_tool_expression(" in ln and "await" in ln)
    assert "suppress" not in call_line


# ── 二、客服腔打回 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "很高兴为您服务。",
        "您好，有什么可以帮您？",
        "希望对您有帮助。",
        "已为您查询到相关信息。",
        "如有需要随时告诉我。",
        "感谢您的咨询。",
        "不客气。有需要再叫我。",
        "随时找我。",
    ],
)
def test_service_tone_hits(text: str) -> None:
    hit = of.check_service_tone(text)
    assert hit is not None and hit.category == "service_tone", text


@pytest.mark.parametrize(
    "text",
    [
        "建议你早点睡。",
        "谢谢。",
        "您今天挺可爱的。",
        "请问您吃了吗",
        "这个方案我觉得可以。",
        "汤面吧，别太油。",
        # 「不客气」只在句首/句末独立成句才算套话；句中"不客气地说"是正常表达
        "不客气地说，这个方案有问题。",
        "不用谢。",
    ],
)
def test_service_tone_does_not_fire_on_normal_speech(text: str) -> None:
    assert of.check_service_tone(text) is None, text


def test_service_tone_rewrite_once_then_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(of, "service_tone_enabled", lambda: True)
    extra: dict = {}
    r1 = pre_send_gate("很高兴为您服务。", extra, channel="main")
    assert r1.decision is GateDecision.REWRITE
    assert r1.policy == "service_tone"
    assert "客服" in r1.feedback

    # 生产里每次 ModelResponse 开头都会 begin_response_batch；重说后仍复犯 → 放行
    output_gate.begin_response_batch(extra)
    r2 = pre_send_gate("很高兴为您服务。", extra, channel="main")
    assert r2.decision is GateDecision.ALLOW


def test_service_tone_skips_tool_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(of, "service_tone_enabled", lambda: True)
    r = pre_send_gate("很高兴为您服务。", {}, channel="tool")
    assert r.decision is GateDecision.ALLOW


def test_service_tone_gate_bag_has_policy_state() -> None:
    # 未登记 PolicyState 会让 _record_block 抛 ValueError
    assert isinstance(GateBag().service_tone.attempts, int)


# ── 三、闲聊短句去句号 ──────────────────────────────────────────────


class _FakeAiCfg:
    def __init__(self, enabled: bool):
        self._enabled = enabled

    def get_config(self, key):
        assert key == "strip_trailing_period"

        class _C:
            data = self._enabled

        return _C()


def _patch_period(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    import gsuid_core.ai_core.configs.ai_config as ac

    monkeypatch.setattr(ac, "ai_config", _FakeAiCfg(enabled))


@pytest.mark.parametrize(
    "raw, expect",
    [
        ("在。", "在"),
        ("早。", "早"),
        ("汤面。", "汤面"),
        ("今天有点累，早点睡吧。", "今天有点累，早点睡吧"),
        # 问号 / 感叹号 / 省略号不动
        ("有事？", "有事？"),
        ("真的！", "真的！"),
        ("困了……", "困了……"),
        # 长句保留句号（要靠它断句）
        ("这道题的答案是先把数据按时间排序，再按用户分组，最后统计每个用户最近一次的出现时间。", None),
        # 列表 / 代码 / 表格保留
        ("- 第一条。", "- 第一条。"),
        ("```py\nprint(1)。\n```", "```py\nprint(1)。\n```"),
    ],
)
def test_strip_casual_period(monkeypatch: pytest.MonkeyPatch, raw: str, expect) -> None:
    from gsuid_core.ai_core.utils import _strip_casual_period

    _patch_period(monkeypatch, True)
    got = _strip_casual_period(raw)
    if expect is None:
        assert got == raw
    else:
        assert got == expect, (raw, got)


def test_strip_period_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from gsuid_core.ai_core.utils import _strip_casual_period

    _patch_period(monkeypatch, False)
    assert _strip_casual_period("在。") == "在。"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
