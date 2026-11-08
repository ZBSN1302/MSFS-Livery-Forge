"""贴图文件分类：根据文件名后缀推断 MSFS 贴图种类（TextureKind）。

MSFS 涂装贴图命名约定（来自真实 2024/2020 涂装包）：

======================  ======================  ============================
后缀                     TextureKind             中文标签（types.py）
======================  ======================  ============================
``*_ALBD``              ALBEDO                  基础色 (ALBD)
``*_COMP``              COMP                    材质 (COMP)
``*_NORM``              NORMAL                  法线 (NORM)
``*_DECAL``             DECAL                   贴花 (DECAL)
``*_EMIS``              EMISSIVE                自发光 (EMIS)
其它                    GENERIC                 通用
======================  ======================  ============================

匹配规则：忽略扩展名、大小写不敏感，识别以下划线/连字符/点/空格分隔的独立
token（例如 ``A320NEO_AIRFRAME_ALBD.PNG`` -> ALBEDO）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from ..types import TextureKind

#: 转换输入支持的图片扩展名（texconv 通过 WIC 读取，这里列出常用格式）。
SUPPORTED_IMAGE_EXTS: frozenset[str] = frozenset(
    {".png", ".tif", ".tiff", ".bmp", ".jpg", ".jpeg", ".tga", ".hdr", ".exr", ".dds"}
)

#: token -> TextureKind，顺序即优先级（先匹配先返回）。
_KIND_TOKENS: tuple[tuple[str, TextureKind], ...] = (
    ("ALBD", TextureKind.ALBEDO),
    ("COMP", TextureKind.COMP),
    ("NORM", TextureKind.NORMAL),
    ("DECAL", TextureKind.DECAL),
    ("EMIS", TextureKind.EMISSIVE),
    # 少数作者会写完整单词；不改变主流后缀的优先级。
    ("EMISSIVE", TextureKind.EMISSIVE),
    ("ALBEDO", TextureKind.ALBEDO),
    ("NORMAL", TextureKind.NORMAL),
)

_TOKEN_RE = {
    token: re.compile(rf"(?:^|[_\-. ]){token}(?:$|[_\-. ])")
    for token, _kind in _KIND_TOKENS
}


def classify(name: str) -> TextureKind:
    """把文件名（或完整路径）分类为 :class:`~liveryforge.types.TextureKind`。"""
    if not name:
        return TextureKind.GENERIC
    stem = Path(name).stem  # 去掉最后一个扩展名：A.PNG -> A；A.PNG.DDS -> A.PNG
    upper = stem.upper()
    for token, kind in _KIND_TOKENS:
        if _TOKEN_RE[token].search(upper):
            return kind
    return TextureKind.GENERIC


def classify_path(path: str | os.PathLike[str]) -> TextureKind:
    """与 :func:`classify` 相同，但显式接受 Path。"""
    return classify(Path(path).name)


def kind_label(kind: TextureKind | str) -> str:
    """返回 TexureKind 的中文标签，例如 ``"基础色 (ALBD)"``。"""
    return TextureKind(kind).label_zh


def describe(name: str) -> tuple[TextureKind, str]:
    """一次返回 ``(kind, 中文标签)``，供 GUI/CLI 列表使用。"""
    kind = classify(name)
    return kind, kind.label_zh


def is_image_file(path: str | os.PathLike[str]) -> bool:
    """判断路径是否是受支持的图片文件（只看扩展名，不读文件内容）。"""
    return Path(path).suffix.lower() in SUPPORTED_IMAGE_EXTS


def iter_image_files(directory: str | os.PathLike[str], *,
                     recursive: bool = False) -> list[Path]:
    """列出目录下的图片文件（按文件名自然排序，排除隐藏文件）。"""
    root = Path(directory)
    if not root.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    files = [
        p for p in root.glob(pattern)
        if p.is_file() and not p.name.startswith(".") and is_image_file(p)
    ]
    return sorted(files, key=lambda p: (str(p.parent).lower(), p.name.lower()))
