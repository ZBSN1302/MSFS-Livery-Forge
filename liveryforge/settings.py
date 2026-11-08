"""用户配置持久化（T3）。

配置以 JSON 保存在 ``%APPDATA%/MSFSLiveryForge/settings.json``：

    {
      "community_dir":    "E:\\历史\\Community",
      "last_output_dir":  "C:\\Users\\me\\Documents\\MSFS Livery Forge",
      "quality_high":     true,
      "formats":          "ktx2",          // ktx2 | dds | both
      "author":           "我的昵称"
    }

设计要点（面向非程序员，任何一步失败都不能让程序打不开）：

* 文件不存在  -> 返回默认配置；
* 文件损坏 / 不是合法 JSON / 顶层不是对象 -> 返回默认配置，不抛异常；
* 单个字段类型不对（例如手改成字符串）-> 该字段回退默认值，其它字段照常加载；
* 保存使用 ``utils.atomic_write_text`` 原子写入，写一半断电不会产生坏文件；
* 支持环境变量 ``MSFSLIVERYFORGE_SETTINGS`` 覆盖配置文件路径（测试/便携模式用）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import APP_NAME, user_config_dir
from .utils import atomic_write_text

SETTINGS_FILENAME = "settings.json"
ENV_SETTINGS_PATH = "MSFSLIVERYFORGE_SETTINGS"

#: 允许的输出格式（与 types.TargetFormat 的 value 一致）
VALID_FORMATS = ("ktx2", "dds", "both")


def settings_path() -> Path:
    """返回配置文件路径（默认 %APPDATA%/MSFSLiveryForge/settings.json）。

    即使配置目录无法创建（只读环境/权限不足）也返回路径而不抛异常，
    只是后续 ``save()`` 会失败——由调用方给出中文提示。
    """
    override = os.environ.get(ENV_SETTINGS_PATH, "").strip()
    if override:
        return Path(override).expanduser()
    try:
        return user_config_dir() / SETTINGS_FILENAME
    except OSError:
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME / SETTINGS_FILENAME


def _clean_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    return default


def _clean_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on", "是"):
            return True
        if low in ("false", "0", "no", "off", "否"):
            return False
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return default


def _clean_format(value: Any, default: str = "ktx2") -> str:
    """容忍字符串、列表等写法，归一到 ktx2/dds/both。"""
    if isinstance(value, str):
        low = value.strip().lower()
        if low in VALID_FORMATS:
            return low
        return default
    if isinstance(value, (list, tuple, set)):
        items = {str(item).strip().lower() for item in value}
        has_ktx2 = "ktx2" in items
        has_dds = "dds" in items
        if has_ktx2 and has_dds:
            return "both"
        if has_dds:
            return "dds"
        if has_ktx2:
            return "ktx2"
    return default


@dataclass
class Settings:
    """GUI/CLI 共用的用户配置。字段都是可 JSON 序列化的基础类型。"""

    community_dir: str = ""
    last_output_dir: str = ""
    quality_high: bool = True
    formats: str = "ktx2"
    author: str = ""

    # --- 附加的便利字段（v1 额外提供，不属于强制契约） ---
    last_texture_dir: str = ""
    last_convert_dir: str = ""
    clone_template_dir: str = ""
    thumbnail_dir: str = ""
    copy_to_community: bool = False
    window_geometry: str = ""
    config_version: int = 1

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "community_dir": self.community_dir,
            "last_output_dir": self.last_output_dir,
            "quality_high": self.quality_high,
            "formats": self.formats,
            "author": self.author,
            "last_texture_dir": self.last_texture_dir,
            "last_convert_dir": self.last_convert_dir,
            "clone_template_dir": self.clone_template_dir,
            "thumbnail_dir": self.thumbnail_dir,
            "copy_to_community": self.copy_to_community,
            "window_geometry": self.window_geometry,
            "config_version": self.config_version,
        }

    # 兼容别名
    as_dict = to_dict

    @classmethod
    def from_dict(cls, raw: Any) -> "Settings":
        """从任意 dict 构造配置；字段缺失或类型错误时回退默认值。"""
        if not isinstance(raw, dict):
            return cls()
        data = {
            "community_dir": _clean_str(raw.get("community_dir")),
            "last_output_dir": _clean_str(raw.get("last_output_dir")),
            "quality_high": _clean_bool(raw.get("quality_high"), True),
            "formats": _clean_format(raw.get("formats")),
            "author": _clean_str(raw.get("author")),
            "last_texture_dir": _clean_str(raw.get("last_texture_dir")),
            "last_convert_dir": _clean_str(raw.get("last_convert_dir")),
            "clone_template_dir": _clean_str(raw.get("clone_template_dir")),
            "thumbnail_dir": _clean_str(raw.get("thumbnail_dir")),
            "copy_to_community": _clean_bool(raw.get("copy_to_community"), False),
            "window_geometry": _clean_str(raw.get("window_geometry")),
        }
        return cls(**data)

    # ------------------------------------------------------------------ 读写
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        """加载配置。任何异常都静默回退默认值，保证程序总能启动。"""
        config_file = Path(path) if path is not None else settings_path()
        try:
            text = config_file.read_text(encoding="utf-8-sig")
        except (FileNotFoundError, IsADirectoryError):
            return cls()
        except (OSError, UnicodeError):
            # 权限不足、路径非法、编码损坏等：使用默认配置继续运行
            return cls()
        try:
            raw = json.loads(text)
        except ValueError:
            # 损坏的 JSON（例如手工编辑出错、写一半断电）：使用默认配置
            return cls()
        return cls.from_dict(raw)

    def save(self, path: str | Path | None = None) -> Path:
        """保存配置并返回实际写入的路径。

        写入失败（只读目录等）抛出 ``OSError``，由调用方决定是否提示；
        正常情况下调用方无需关心，失败不影响本次会话使用内存中的配置。
        """
        config_file = Path(path) if path is not None else settings_path()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(config_file, text, encoding="utf-8")
        return config_file

    # ------------------------------------------------------------------ 便捷
    def clone(self) -> "Settings":
        return Settings(**self.to_dict())

    def add_recent_author(self, author: str) -> None:
        """记住作者名（GUI 与 CLI 共享）。"""
        author = (author or "").strip()
        if author:
            self.author = author


__all__ = [
    "Settings",
    "settings_path",
    "settings_dir",
    "SETTINGS_FILENAME",
    "ENV_SETTINGS_PATH",
    "VALID_FORMATS",
]


def settings_dir() -> Path:
    """配置文件所在目录（GUI「打开配置目录」按钮用）。"""
    return settings_path().parent


# 防止未使用导入告警（APP_NAME 供外部参考）
_ = APP_NAME
