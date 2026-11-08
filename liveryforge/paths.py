"""路径解析：项目根、内置工具、资源、用户配置目录。"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "MSFSLiveryForge"
APP_DISPLAY_NAME = "MSFS 2024 涂装工坊"
VERSION = "1.0.0"

# liveryforge/paths.py -> 项目根 = 上两级
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
TEXCONV_DIR = TOOLS_DIR / "texconv"
SAMPLES_DIR = PROJECT_ROOT / "samples"
DOCS_DIR = PROJECT_ROOT / "docs"
PROFILES_DIR = PROJECT_ROOT / "liveryforge" / "profiles" / "data"


def texconv_path() -> Path:
    """内置 texconv.exe 的路径（不校验存在性）。"""
    return TEXCONV_DIR / "texconv.exe"


def user_config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    """默认输出目录：我的文档/MSFS Livery Forge。"""
    base = Path.home() / "Documents"
    if not base.exists():
        base = PROJECT_ROOT / "output"
    path = base / "MSFS Livery Forge"
    return path
