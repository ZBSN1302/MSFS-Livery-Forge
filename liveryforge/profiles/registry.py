"""机型 profile 注册表：``list_profiles()`` / ``get_profile(id)``。

数据来自 ``liveryforge/profiles/data/*.json``（真实已安装涂装包提取）。
Profile JSON 除 ``types.Profile`` 字段外，还允许以下扩展键（本模块挂到 Profile
实例属性上，``Profile.to_dict()`` 不会输出，但渲染代码可用）：

- ``manufacturer``                 manifest.manufacturer（如 Cessna/Airbus/Boeing）
- ``minimum_game_version``         manifest 最低游戏版本
- ``minimum_compatibility_version``
- ``livery_cfg_template``          2024 livery.cfg 模板字符串（字段占位符）
- ``aircraft_cfg_template``        2020 aircraft.cfg 模板字符串
- ``sort_order``                   列表排序
"""
from __future__ import annotations

import json
from pathlib import Path

from liveryforge import paths
from liveryforge.types import LiveryForgeError, Profile

#: 扩展键（会挂到 Profile 实例上）
EXTRA_KEYS = (
    "manufacturer",
    "minimum_game_version",
    "minimum_compatibility_version",
    "livery_cfg_template",
    "aircraft_cfg_template",
    "sort_order",
)


class ProfileNotFound(LiveryForgeError):
    """指定的机型 profile 不存在。"""


_cache: dict[str, dict] | None = None


def _data_dir() -> Path:
    return Path(paths.PROFILES_DIR)


def _load_all() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    data: dict[str, dict] = {}
    data_dir = _data_dir()
    if data_dir.is_dir():
        for path in sorted(data_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    raw = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            data[str(raw["id"])] = raw
    _cache = data
    return data


def reload_profiles() -> None:
    """清除缓存（测试/动态新增 profile 时用）。"""
    global _cache
    _cache = None


def _make_profile(raw: dict) -> Profile:
    profile = Profile.from_dict(raw)
    extra = {key: raw[key] for key in EXTRA_KEYS if key in raw}
    profile._extra = extra  # type: ignore[attr-defined]
    for key, value in extra.items():
        setattr(profile, key, value)
    return profile


def list_profiles() -> list[Profile]:
    """返回全部内置机型 profile（按 sort_order、name 排序）。"""
    profiles = [_make_profile(raw) for raw in _load_all().values()]
    profiles.sort(key=lambda p: (getattr(p, "sort_order", 99), p.name))
    return profiles


def get_profile(profile_id: str) -> Profile:
    """按 id 取 profile；不存在抛 :class:`ProfileNotFound`（中文消息）。"""
    raw = _load_all().get(str(profile_id))
    if raw is None:
        available = "、".join(sorted(_load_all())) or "（无）"
        raise ProfileNotFound(
            f"找不到机型 profile：{profile_id}。可用机型：{available}")
    return _make_profile(raw)


def has_profile(profile_id: str) -> bool:
    return str(profile_id) in _load_all()


def get_profile_extra(profile_id: str) -> dict:
    """返回 profile 的扩展元数据（manifest 字段/模板等）；不存在返回 {}。"""
    raw = _load_all().get(str(profile_id))
    if raw is None:
        return {}
    return {key: raw[key] for key in EXTRA_KEYS if key in raw}


def profile_ids() -> list[str]:
    return [p.id for p in list_profiles()]


__all__ = [
    "EXTRA_KEYS",
    "ProfileNotFound",
    "get_profile",
    "get_profile_extra",
    "has_profile",
    "list_profiles",
    "profile_ids",
    "reload_profiles",
]
