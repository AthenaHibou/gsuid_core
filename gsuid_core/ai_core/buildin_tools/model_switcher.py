"""模型切换工具模块

提供 AI 按用户要求**临时切换本会话执行模型**的能力：
- switch_model: 校验目标配置并登记切换请求，终止当前 run 由框架换模型重跑本轮消息
- list_available_models: 枚举全部可用模型配置（标注当前使用 / 主备模型）

切换是**单轮临时**的：重跑结束后框架自动还原原模型，不修改任何持久化配置，
也不影响其他会话。控制流：工具写入 ``agent._pending_model_switch`` 后抛出
``ModelSwitchSignal``（见 gs_agent.py）终止当前 generation，``run()`` 捕获后
以 override 模型重跑同一条用户消息，并在 run 结束时还原。
"""

from typing import Optional

from pydantic_ai import RunContext

from gsuid_core.logger import logger
from gsuid_core.ai_core.models import ToolContext
from gsuid_core.ai_core.register import ai_tools

# 三个受支持的 provider，与 configs/models.py 的 SUPPORTED_PROVIDERS 对齐
_PROVIDERS = ("openai", "anthropic", "gemini")


def _list_config_names(provider: str) -> list[str]:
    """列出某 provider 下的全部配置名，provider 不可用时返回空列表。"""
    from gsuid_core.ai_core.configs.provider_config_manager import (
        list_available_provider_configs,
    )

    try:
        return list_available_provider_configs(provider)
    except Exception:  # noqa: BLE001
        return []


def _resolve_target_full_name(target: str) -> Optional[str]:
    """把模型给的目标解析为唯一的 provider++name 全名；解析失败返回 None。

    支持 ``provider++name`` 全名（校验配置存在）与裸配置名（三 provider 中
    唯一同名才接受，避免跨 provider 同名歧义）。
    """
    from gsuid_core.ai_core.configs.models import (
        parse_provider_config_name,
        format_provider_config_name,
    )

    target = (target or "").strip()
    if not target:
        return None

    if "++" in target:
        try:
            provider, name = parse_provider_config_name(target)
        except Exception:  # noqa: BLE001 - 未知 provider 视为无效目标
            return None
        if name in _list_config_names(provider):
            return format_provider_config_name(provider, name)
        return None

    matches = [
        format_provider_config_name(p, target)
        for p in _PROVIDERS
        if target in _list_config_names(p)
    ]
    return matches[0] if len(matches) == 1 else None


def _format_available_models(current_full_name: Optional[str]) -> str:
    """生成可用配置清单文本，标注当前使用中的模型与各任务级别的主/备模型。"""
    from gsuid_core.ai_core.configs.models import (
        get_config_name_for_task,
        format_provider_config_name,
        get_2nd_config_name_for_task,
    )

    lines: list[str] = []
    for provider in _PROVIDERS:
        for name in _list_config_names(provider):
            full = format_provider_config_name(provider, name)
            marks: list[str] = []
            if current_full_name and full == current_full_name:
                marks.append("当前使用")
            for level in ("high", "low"):
                try:
                    if get_config_name_for_task(level) == full:
                        marks.append(f"{level}级主模型")
                    second = get_2nd_config_name_for_task(level)
                    if second and second == full:
                        marks.append(f"{level}级备用模型")
                except Exception:  # noqa: BLE001
                    pass
            suffix = f"（{'、'.join(marks)}）" if marks else ""
            lines.append(f"- {full}{suffix}")
    return "\n".join(lines) if lines else "（当前无可用模型配置）"


def _get_calling_agent(ctx: RunContext[ToolContext]):
    """按 parent_session_id 取调用方 GsCoreAIAgent；取不到返回 None。"""
    from gsuid_core.ai_core.session_registry import get_ai_session_registry

    session_id = ctx.deps.parent_session_id
    if not session_id:
        return None
    try:
        return get_ai_session_registry().get_ai_session(session_id)
    except Exception:  # noqa: BLE001
        return None


@ai_tools(category="buildin", capability_domain="模型切换")
async def list_available_models(ctx: RunContext[ToolContext]) -> str:
    """列出当前所有可用的模型配置，供切换模型时选择

    当用户想换模型但没说清具体名字，或需要确认有哪些模型可选时调用。
    返回全部模型配置清单（provider++name 全名），并标注当前会话正在使用的
    模型与各任务级别的主/备用模型。

    Args:
        ctx: 工具执行上下文

    Returns:
        可用模型配置清单文本，每行一个 provider++name 全名，可直接作为
        switch_model 的 target 参数
    """
    agent = _get_calling_agent(ctx)
    current = getattr(agent, "model_config_name", None) if agent is not None else None
    return _format_available_models(current)


@ai_tools(category="buildin", capability_domain="模型切换")
async def switch_model(ctx: RunContext[ToolContext], target: str) -> str:
    """应用户要求切换本会话的对话模型（临时生效，本轮回答完自动还原）

    仅当用户明确要求"切换/换个模型、用备用模型或更强的模型回答"时调用。
    切换由系统完成：登记后当前回复会被终止，用户同一条消息立即交给新模型
    重新处理，本轮结束后自动还原原模型。不确定有哪些模型时先调用
    list_available_models 查询。

    Args:
        ctx: 工具执行上下文
        target: 目标模型配置名，推荐 provider++name 全名（如 openai++backup）；
            只传裸配置名时若跨 provider 存在同名配置将解析失败

    Returns:
        失败时的原因说明（含可选清单）；成功时本函数不会正常返回——
        框架会终止本次回复并换用目标模型重新处理用户消息
    """
    agent = _get_calling_agent(ctx)
    if agent is None or not hasattr(agent, "_pending_model_switch"):
        return "⚠️ 当前会话不支持切换模型（未找到会话实例）"

    target_full = _resolve_target_full_name(target)
    if target_full is None:
        current = getattr(agent, "model_config_name", None)
        return (
            f"⚠️ 未找到模型配置「{target}」。可用配置如下，请原样选择其中一个"
            f" provider++name 再调用本工具：\n{_format_available_models(current)}"
        )

    current = getattr(agent, "model_config_name", None)
    if current and target_full == current:
        return f"当前会话已在使用 {target_full}，无需切换"

    # 动态导入：ModelSwitchSignal 定义在 gs_agent，模块级导入会构成循环依赖
    from gsuid_core.ai_core.gs_agent import ModelSwitchSignal

    logger.info(f"模型切换：会话 {getattr(agent, 'session_id', '?')} 请求切换到 {target_full}")
    agent._pending_model_switch = target_full
    raise ModelSwitchSignal(target_full)
