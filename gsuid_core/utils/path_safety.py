"""文件系统路径围栏：拒绝穿越、绝对路径换根、Windows 盘符/保留名。

从上游 gsuid-core 移植（裁剪版，仅保留路径围栏核心函数）。
所有把用户输入拼进 ``Path`` / ``open`` / ``FileResponse`` / ``unlink`` 的
webconsole 及插件页面路径必须走这里，不要自己 ``root / user``。
"""

from __future__ import annotations

import os
from typing import Iterable, Optional
from pathlib import Path


class PathEscapeError(ValueError):
    """用户路径越出允许的根目录，或文件名本身非法。"""


_WIN_RESERVED_CHARS = set('<>:"|?*')
_WIN_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def _has_ctrl(name: str) -> bool:
    return any((ord(c) < 0x20) or (ord(c) == 0x7F) for c in name)


def _is_win_reserved_part(part: str) -> bool:
    stem = part.split(".", 1)[0].upper()
    return stem in _WIN_RESERVED_NAMES


def is_safe_relpath(rel: str) -> bool:
    """相对路径是否可拼到根下：禁止绝对路径、``.`` / ``..`` 段、分隔盘符、控制字符。

    允许多段（``assets/js/a.js``）和 Unicode 文件名（``下载.jpg``）。
    ``foo..jpg`` 这类「子串含 .. 但不是独立段」合法。
    """
    if not isinstance(rel, str):
        return False
    name = rel.strip()
    if not name or name != rel:
        return False
    if _has_ctrl(name):
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        return False
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    if any(c in name for c in _WIN_RESERVED_CHARS):
        return False
    parts = Path(normalized).parts
    if not parts:
        return False
    for part in parts:
        if part in {"", ".", ".."}:
            return False
        if _has_ctrl(part) or _is_win_reserved_part(part):
            return False
    return True


def is_safe_filename(name: str) -> bool:
    """单段文件名（不含任何路径分隔）。"""
    if not is_safe_relpath(name):
        return False
    return "/" not in name.replace("\\", "/")


def is_under_root(path: Path, root: Path) -> bool:
    """``path`` 解析后是否落在 ``root`` 子树（含自身）。Windows 上按 normcase 比较。"""
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(base)
        return True
    except ValueError:
        pass
    if os.name == "nt":
        r = os.path.normcase(str(resolved))
        b = os.path.normcase(str(base))
        return r == b or r.startswith(b + os.sep)
    return False


def safe_join(root: Path, *parts: str) -> Path:
    """把相对段拼到 ``root`` 下并 ``resolve``；越界或非法段抛 ``PathEscapeError``。"""
    if not parts:
        raise PathEscapeError("路径为空")
    try:
        root_res = root.resolve()
    except OSError as e:
        raise PathEscapeError(f"根目录不可用: {e}") from e
    rel = "/".join(str(p).replace("\\", "/") for p in parts if p is not None and str(p) != "")
    if not rel:
        raise PathEscapeError("路径为空")
    if not is_safe_relpath(rel):
        raise PathEscapeError("非法路径")
    candidate = root_res.joinpath(*Path(rel.replace("\\", "/")).parts)
    try:
        resolved = candidate.resolve()
    except OSError as e:
        raise PathEscapeError(f"路径不可用: {e}") from e
    if not is_under_root(resolved, root_res):
        raise PathEscapeError("越界路径")
    return resolved


def confine_to_root(user_path: str, root: Path) -> Path:
    """允许相对路径或已经落在 ``root`` 内的绝对路径；其它一律拒绝。"""
    raw = (user_path or "").strip()
    if not raw:
        raise PathEscapeError("路径为空")
    p = Path(raw)
    try:
        root_res = root.resolve()
    except OSError as e:
        raise PathEscapeError(f"根目录不可用: {e}") from e
    if p.is_absolute() or p.drive:
        try:
            resolved = p.resolve()
        except OSError as e:
            raise PathEscapeError(f"路径不可用: {e}") from e
        if not is_under_root(resolved, root_res):
            raise PathEscapeError("越界路径")
        return resolved
    return safe_join(root_res, raw)


def resolve_under_root(raw: str, root: Path, *, aliases: frozenset[str] = frozenset({"."})) -> Path:
    """相对名若在 ``aliases`` 中则落到 ``root`` 自身，否则 ``confine_to_root``。"""
    text = raw.strip().rstrip("/\\")
    if text in aliases:
        try:
            return root.resolve()
        except OSError as e:
            raise PathEscapeError(f"根目录不可用: {e}") from e
    return confine_to_root(raw.strip(), root)


def ensure_under_any(path: Path, roots: Iterable[Path]) -> Path:
    """已有路径必须落在任一允许根下。"""
    try:
        resolved = path.resolve()
    except OSError as e:
        raise PathEscapeError(f"路径不可用: {e}") from e
    for root in roots:
        if is_under_root(resolved, root):
            return resolved
    raise PathEscapeError("越界路径")


__all__ = [
    "PathEscapeError",
    "is_safe_relpath",
    "is_safe_filename",
    "is_under_root",
    "safe_join",
    "confine_to_root",
    "resolve_under_root",
    "ensure_under_any",
]
