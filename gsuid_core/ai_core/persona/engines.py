"""人格对话引擎解析（core / tavern）。

引擎是“怎么编排一轮对话”，不是人格本身：

- ``gscore``：群聊 Agent 档位，现有 system/历史/输入/输出；
- ``tavern``：酒馆档位，原生示例 + PHI + 干净输入 + 角色化输出。

三档优先级（高 → 低）：

1. 目标级覆盖：群聊看 ``group:<group_id>``，私聊看 ``user:<user_id>``；
2. 人格默认：``persona/<name>/engines.json`` 的 ``default``，缺省回退旧的
   ``config.json -> prompt_profile``；
3. 全局默认：``ai_config.engine_default``。

总开关在解析入口统一收口：``ai_config.enable`` 关掉时任何引擎都不走；
``ai_config.engine_tavern_enable`` 关掉时所有 tavern 解析结果回退 gscore。
群聊 session 是整群共享的，所以群聊只认 group 覆盖，不认个人 user 覆盖——
否则一个人切引擎会影响整个群的历史形态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from gsuid_core.i18n import t
from gsuid_core.logger import logger
from gsuid_core.models import Event

from ..configs.ai_config import ai_config
from ..resource import PERSONA_PATH
from .config import persona_config_manager

ENGINE_CORE = "gscore"
ENGINE_TAVERN = "tavern"
_ENGINES = frozenset({ENGINE_CORE, ENGINE_TAVERN})
_MAX_ENGINE_BYTES = 100_000
_MAX_OVERRIDES = 200


@dataclass
class EngineConfig:
    """`persona/<name>/engines.json` 的规范化视图。"""

    default: str = ""
    # key = "group:<id>" / "user:<id>"，value = gscore / tavern
    overrides: Dict[str, str] = field(default_factory=dict)


def engine_key(target_type: str, target_id: str) -> str:
    return f"{target_type}:{target_id}"


def _valid_engine(value: object) -> Optional[str]:
    if isinstance(value, str) and value in _ENGINES:
        return value
    return None


def _split_engine_key(key: object) -> Optional[tuple[str, str]]:
    if not isinstance(key, str):
        return None
    target_type, sep, target_id = key.partition(":")
    if not sep or target_type not in ("group", "user") or not target_id.strip():
        return None
    return target_type, target_id.strip()


def _path_for(persona_name: str):
    return PERSONA_PATH / persona_name / "engines.json"


def load_engine_config(persona_name: str) -> EngineConfig:
    path = _path_for(persona_name)
    if not path.is_file():
        return EngineConfig()
    try:
        if path.stat().st_size > _MAX_ENGINE_BYTES:
            logger.warning(t("log.persona.engines_config_too_large", persona_name=persona_name))
            return EngineConfig()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as e:
        logger.warning(t("log.persona.engines_config_parse_fail", persona_name=persona_name, e=e))
        return EngineConfig()
    if not isinstance(raw, dict):
        return EngineConfig()

    default = _valid_engine(raw.get("default")) or ""
    overrides: Dict[str, str] = {}
    raw_overrides = raw.get("overrides")
    if isinstance(raw_overrides, dict):
        for key, value in list(raw_overrides.items())[:_MAX_OVERRIDES]:
            if _split_engine_key(key) is None:
                continue
            engine = _valid_engine(value)
            if engine is not None:
                overrides[str(key)] = engine
    return EngineConfig(default=default, overrides=overrides)


def save_engine_config(persona_name: str, config: EngineConfig) -> None:
    path = _path_for(persona_name)
    payload = {
        "default": config.default,
        "overrides": dict(sorted(config.overrides.items())),
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(raw.encode("utf-8")) > _MAX_ENGINE_BYTES:
        raise ValueError("engines.json 过大")
    path.write_text(raw, encoding="utf-8")


def _persona_default(persona_name: str) -> str:
    try:
        value = persona_config_manager.get_config(persona_name).get_config("prompt_profile").data
    except Exception:  # noqa: BLE001 - 配置损坏时回退全局默认，不影响主链路
        return ""
    return value if isinstance(value, str) and value in _ENGINES else ""


async def resolve_engine(event: Event, persona_name: str) -> str:
    """解析本次会话应该使用的引擎（总开关在此统一收口）。"""
    if not bool(ai_config.get_config("enable").data):
        return ENGINE_CORE

    cfg = load_engine_config(persona_name)
    if event.group_id:
        key = engine_key("group", str(event.group_id))
    else:
        key = engine_key("user", str(event.user_id))
    engine = cfg.overrides.get(key) or cfg.default or _persona_default(persona_name)
    if not engine:
        global_default = ai_config.get_config("engine_default").data
        engine = global_default if isinstance(global_default, str) else ENGINE_CORE
    if engine not in _ENGINES:
        engine = ENGINE_CORE

    # 酒馆引擎总开关：关掉时所有 tavern 分配都回退 core，但 core 会话不受影响。
    if engine == ENGINE_TAVERN and not bool(ai_config.get_config("engine_tavern_enable").data):
        return ENGINE_CORE
    return engine


def engine_config_to_api(persona_name: str) -> Dict[str, Any]:
    cfg = load_engine_config(persona_name)
    return {
        "default": cfg.default,
        "persona_default": _persona_default(persona_name),
        "overrides": [
            {"target_type": key.split(":", 1)[0], "target_id": key.split(":", 1)[1], "engine": value}
            for key, value in sorted(cfg.overrides.items())
        ],
    }
