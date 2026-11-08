"""manifest.json 生成。

规则依据真实 2024 包（livery-longitude-n233cl / Captain Simon PMDG / KFS A380 /
jeffs A350）：
- 字段顺序：dependencies, content_type, title, manufacturer, creator,
  package_version, minimum_game_version, minimum_compatibility_version,
  export_type, builder, package_order_hint, release_notes, total_package_size。
- 缩进 2 空格、CRLF、UTF-8 无 BOM、末尾无换行。
- ``total_package_size`` 是包内**所有文件**（含 layout.json 与 manifest.json 自身）
  字节数之和的零填充十进制字符串；真实样本为 **20 位**（如
  ``"00000000000597290953"``），故本模块常量取 20（按真实样本优先）。
"""
from __future__ import annotations

from pathlib import Path

from liveryforge import utils
from liveryforge.types import LiveryInfo, Profile

MANIFEST_FILENAME = "manifest.json"

BUILDER = "Microsoft Flight Simulator 2024"
EXPORT_TYPE = "Community"

#: 真实样本 total_package_size 位数（20 位零填充）
TOTAL_PACKAGE_SIZE_WIDTH = 20

DEFAULT_MIN_GAME_VERSION_2024 = "1.4.20"
DEFAULT_MIN_COMPAT_VERSION_2024 = "4.20.0.73"
DEFAULT_MIN_GAME_VERSION_2020 = "1.23.12"
DEFAULT_MIN_COMPAT_VERSION_2020 = "0.23.12.0"

DEFAULT_PACKAGE_ORDER_HINT = "SIMOBJECTS_PATCH"

_RELEASE_NOTES = {
    "neutral": {
        "LastUpdate": "",
        "OlderHistory": "",
    }
}


def format_total_package_size(total: int, width: int = TOTAL_PACKAGE_SIZE_WIDTH) -> str:
    """把总字节数格式化为零填充十进制字符串（真实为 20 位）。"""
    if total < 0:
        raise ValueError("总大小不能为负数")
    return str(int(total)).zfill(width)


def compute_total_package_size(package_dir: Path, *, exclude_manifest: bool = True) -> int:
    """统计包内所有文件字节数之和。

    :param exclude_manifest: True 时先排除根目录 manifest.json（写 manifest 时用，
        之后再把它自身的字节数加回去）。
    """
    package_dir = Path(package_dir)
    total = 0
    for path in package_dir.rglob("*"):
        if not path.is_file():
            continue
        if exclude_manifest and path.name.lower() == MANIFEST_FILENAME and path.parent == package_dir:
            continue
        total += path.stat().st_size
    return total


def build_manifest(profile: Profile,
                   info: LiveryInfo,
                   total_package_size: int = 0,
                   *,
                   manufacturer: str | None = None,
                   content_type: str | None = None,
                   package_version: str | None = None,
                   minimum_game_version: str | None = None,
                   minimum_compatibility_version: str | None = None,
                   package_order_hint: str | None = None,
                   dependencies: list[str] | None = None,
                   release_notes: dict | None = None) -> dict:
    """构造 manifest.json 的 dict（字段顺序与真实样本一致）。"""
    generation = str(profile.generation or "2024")
    default_game = (DEFAULT_MIN_GAME_VERSION_2020 if generation == "2020"
                    else DEFAULT_MIN_GAME_VERSION_2024)
    default_compat = (DEFAULT_MIN_COMPAT_VERSION_2020 if generation == "2020"
                      else DEFAULT_MIN_COMPAT_VERSION_2024)
    return {
        "dependencies": list(dependencies or []),
        "content_type": content_type or profile.content_type or "LIVERY",
        "title": info.title,
        "manufacturer": manufacturer or profile.name,
        "creator": info.author or profile.creator_folder or "liveryforge",
        "package_version": package_version or info.version or "1.0",
        "minimum_game_version": minimum_game_version or default_game,
        "minimum_compatibility_version":
            minimum_compatibility_version or default_compat,
        "export_type": EXPORT_TYPE,
        "builder": BUILDER,
        "package_order_hint": (package_order_hint if package_order_hint is not None
                               else profile.package_order_hint),
        "release_notes": release_notes if release_notes is not None else dict(_RELEASE_NOTES),
        "total_package_size": format_total_package_size(total_package_size),
    }


def render_manifest(manifest: dict) -> str:
    """渲染 manifest.json 文本（2 空格缩进、CRLF、无尾换行）。"""
    import json
    return json.dumps(manifest, indent=2, ensure_ascii=False).replace("\n", "\r\n")


def write_manifest(package_dir: Path, profile: Profile, info: LiveryInfo,
                   *, manufacturer: str | None = None,
                   content_type: str | None = None,
                   package_version: str | None = None,
                   minimum_game_version: str | None = None,
                   minimum_compatibility_version: str | None = None,
                   package_order_hint: str | None = None,
                   dependencies: list[str] | None = None) -> tuple[Path, dict]:
    """生成并写入 manifest.json，返回 (路径, manifest dict)。

    先统计除 manifest 外所有文件（含 layout.json）的字节和，再把 manifest 自身
    渲染尺寸加进去；total_package_size 为固定宽度，故一次计算即精确。
    """
    package_dir = Path(package_dir)
    manifest = build_manifest(
        profile, info, total_package_size=0,
        manufacturer=manufacturer,
        content_type=content_type,
        package_version=package_version,
        minimum_game_version=minimum_game_version,
        minimum_compatibility_version=minimum_compatibility_version,
        package_order_hint=package_order_hint,
        dependencies=dependencies,
    )
    # 用占位总大小渲染一次，得到 manifest 自身字节数（总大小字段定宽，长度不变）
    manifest["total_package_size"] = format_total_package_size(0)
    probe = render_manifest(manifest).encode("utf-8")
    others = compute_total_package_size(package_dir, exclude_manifest=True)
    manifest["total_package_size"] = format_total_package_size(others + len(probe))
    text = render_manifest(manifest)
    if len(text.encode("utf-8")) != len(probe):
        # 理论上不会发生（定宽数字）；保险起见重算一次
        others = compute_total_package_size(package_dir, exclude_manifest=True)
        manifest["total_package_size"] = format_total_package_size(
            others + len(text.encode("utf-8")))
        text = render_manifest(manifest)
    target = package_dir / MANIFEST_FILENAME
    utils.atomic_write_bytes(target, text.encode("utf-8"))
    return target, manifest


def read_manifest(package_dir: Path) -> dict:
    return utils.read_json(Path(package_dir) / MANIFEST_FILENAME)
