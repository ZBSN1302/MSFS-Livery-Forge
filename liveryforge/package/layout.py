"""layout.json 生成与校验。

规则依据（真实包 + tools/MSFSLayoutGenerator 实测）：
- 顶层只有 ``{"content": [...]}``；每项 ``{"path": ..., "size": ..., "date": ...}``。
- ``path`` 用正斜杠 ``/``，相对包根。
- ``size`` = 文件实际字节数。
- ``date`` = 文件 mtime 的 Windows FILETIME（1601 年起，UTC，100ns），
  精确算法 ``st_mtime_ns // 100 + 116444736000000000``。
- **不包含**根目录的 ``manifest.json`` 与 ``layout.json`` 自身（真实包一致）。
- 条目按 path 不区分大小写排序（确定、可复现；真实包大多如此）。
- 文件编码 UTF-8 无 BOM；2 空格缩进、LF 换行、末尾无换行。
"""
from __future__ import annotations

import json
from pathlib import Path

from liveryforge import utils

#: 根目录下不写入 layout 的文件（真实包验证）
EXCLUDED_ROOT_FILES = frozenset({"manifest.json", "layout.json"})

#: FILETIME 纪元偏移（秒）
FILETIME_EPOCH_OFFSET_SECONDS = 11644473600

LAYOUT_FILENAME = "layout.json"


def _filetime_from_stat(st) -> int:
    """st_mtime_ns -> Windows FILETIME（100ns）。精确、无 float 误差。"""
    return int(st.st_mtime_ns) // 100 + FILETIME_EPOCH_OFFSET_SECONDS * 10_000_000


def iter_package_files(package_dir: Path) -> list[Path]:
    """递归列出包内需要写入 layout 的文件（排除根目录 layout/manifest）。"""
    package_dir = Path(package_dir)
    files: list[Path] = []
    for path in package_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(package_dir)
        if len(rel.parts) == 1 and rel.name.lower() in EXCLUDED_ROOT_FILES:
            continue
        files.append(path)
    files.sort(key=lambda p: p.relative_to(package_dir).as_posix().casefold())
    return files


def generate_layout(package_dir: Path) -> dict:
    """扫描包目录，生成 layout.json 对应的 dict（未写盘）。"""
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        raise NotADirectoryError(f"包目录不存在：{package_dir}")
    content = []
    for path in iter_package_files(package_dir):
        st = path.stat()
        content.append(
            {
                "path": path.relative_to(package_dir).as_posix(),
                "size": int(st.st_size),
                "date": _filetime_from_stat(st),
            }
        )
    return {"content": content}


def render_layout(layout: dict) -> str:
    """渲染 layout.json 文本（2 空格缩进、LF、无尾换行）。"""
    return json.dumps(layout, indent=2, ensure_ascii=False)


def write_layout(package_dir: Path, layout: dict | None = None) -> Path:
    """生成并原子写入 ``<package_dir>/layout.json``，返回路径。"""
    package_dir = Path(package_dir)
    if layout is None:
        layout = generate_layout(package_dir)
    target = package_dir / LAYOUT_FILENAME
    utils.atomic_write_bytes(target, render_layout(layout).encode("utf-8"))
    return target


def read_layout(package_dir: Path) -> dict:
    return utils.read_json(Path(package_dir) / LAYOUT_FILENAME)


def verify_layout(package_dir: Path, layout_json: dict | None = None,
                  *, date_tolerance: int = 0) -> list[str]:
    """校验 layout.json 与包内实际文件是否一致，返回中文问题列表（空=通过）。

    :param package_dir: 包根目录
    :param layout_json: 已解析的 layout.json；None 时自动读取文件
    :param date_tolerance: date 允许误差（100ns 单位）。严格模式为 0；
        真实 Longitude 包因旧工具时区偏移会有固定 6h 偏差，可用大 tolerance。
    """
    package_dir = Path(package_dir)
    problems: list[str] = []
    if layout_json is None:
        layout_path = package_dir / LAYOUT_FILENAME
        if not layout_path.exists():
            return [f"缺少 layout.json：{layout_path}"]
        try:
            layout_json = utils.read_json(layout_path)
        except (OSError, ValueError) as exc:
            return [f"layout.json 无法解析：{exc}"]

    if not isinstance(layout_json, dict) or not isinstance(layout_json.get("content"), list):
        return ['layout.json 结构无效：应为 {"content": [...]}']

    entries = layout_json["content"]
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"第 {index} 条不是对象")
            continue
        missing = [k for k in ("path", "size", "date") if k not in entry]
        if missing:
            problems.append(f"第 {index} 条缺少字段：{', '.join(missing)}")
            continue
        rel = entry["path"]
        if not isinstance(rel, str):
            problems.append(f"第 {index} 条 path 不是字符串")
            continue
        if "\\" in rel:
            problems.append(f"path 使用了反斜杠：{rel}")
        key = rel.casefold()
        if key in seen:
            problems.append(f"path 重复：{rel}")
        seen.add(key)
        target = package_dir / rel
        if not target.is_file():
            problems.append(f"layout 中的文件不存在：{rel}")
            continue
        st = target.stat()
        if int(entry["size"]) != int(st.st_size):
            problems.append(
                f"size 不符：{rel}（layout={entry['size']}，实际={st.st_size}）")
        expected_date = _filetime_from_stat(st)
        try:
            actual_date = int(entry["date"])
        except (TypeError, ValueError):
            problems.append(f"date 不是整数：{rel}")
            continue
        if abs(expected_date - actual_date) > date_tolerance:
            problems.append(
                f"date 不符：{rel}（layout={actual_date}，实际={expected_date}）")

    # 反向检查：磁盘上有、但 layout 中没有（排除根 layout/manifest）
    for path in iter_package_files(package_dir):
        rel = path.relative_to(package_dir).as_posix()
        if rel.casefold() not in seen:
            problems.append(f"文件未写入 layout：{rel}")
    return problems


def is_sorted(layout: dict) -> bool:
    """条目是否按 path 不区分大小写排序。"""
    paths = [e.get("path", "") for e in layout.get("content", []) if isinstance(e, dict)]
    return paths == sorted(paths, key=str.casefold)
