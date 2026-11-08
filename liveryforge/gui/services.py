"""GUI / CLI 共用的服务层（T3）。

这一层负责把「界面/命令行」和「纹理引擎 T1 / 涂装包引擎 T2 / 机型 registry」解耦：

* 引擎模块**懒加载**：T1/T2 尚未实现时，GUI 仍能正常打开，CLI 给出中文提示；
* 贴图扫描/分类在引擎缺席时使用内置文件名规则降级，GUI 依然可以显示列表；
* Community 目录探测优先调用 ``liveryforge.package.simenv``，缺席时回退到
  读取 ``%APPDATA%\\Microsoft Flight Simulator 2024\\UserCfg.opt``；
* 所有面向用户的异常统一为 :class:`liveryforge.types.LiveryForgeError`（中文）。

本模块不导入 tkinter，可安全地被 CLI 使用。
"""
from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..paths import (
    APP_NAME,
    APP_DISPLAY_NAME,
    PROJECT_ROOT,
    SAMPLES_DIR,
    default_output_dir,
    texconv_path,
    user_config_dir,
)
from ..types import (
    BuildRequest,
    BuildResult,
    ConvertOptions,
    LiveryForgeError,
    LiveryInfo,
    PackageError,
    Profile,
    TargetFormat,
    TextureAsset,
    TextureError,
    TextureKind,
)
from ..utils import human_size

#: GUI/CLI 认可的输入贴图扩展名（大小写不敏感）
TEXTURE_EXTENSIONS = (".png", ".tif", ".tiff")

ProgressCallback = Callable[[str, "float | None"], None]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _module_available(dotted: str) -> bool:
    """不导入目标模块，仅检查它是否可被找到（命名空间包也可）。"""
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _try_import(dotted: str):
    try:
        return importlib.import_module(dotted)
    except Exception:  # noqa: BLE001 - 引擎未就绪/自身有 bug 都不能让界面崩溃
        return None


def is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


# ---------------------------------------------------------------------------
# 机型 profile
# ---------------------------------------------------------------------------
def _normalize_profile(obj: Any) -> Profile | None:
    """把 registry 返回的 Profile / dict / 数据类统一成 types.Profile。"""
    if isinstance(obj, Profile):
        return obj
    if isinstance(obj, dict):
        try:
            return Profile.from_dict(obj)
        except (TypeError, ValueError):
            return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        try:
            return Profile.from_dict(dataclasses.asdict(obj))
        except (TypeError, ValueError):
            return None
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return Profile.from_dict(to_dict())
        except (TypeError, ValueError):
            return None
    return None


def _profiles_from_data_dir() -> list[Profile]:
    """直接读取 liveryforge/profiles/data/*.json（registry 未就绪时的兜底）。"""
    from ..paths import PROFILES_DIR

    if not PROFILES_DIR.is_dir():
        return []
    profiles: list[Profile] = []
    try:
        files = sorted(PROFILES_DIR.glob("*.json"))
    except OSError:
        return []
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        items = raw.get("profiles") if isinstance(raw, dict) and isinstance(raw.get("profiles"), list) else None
        if items is None:
            items = [raw] if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        for item in items:
            profile = _normalize_profile(item)
            if profile is not None and profile.id:
                profiles.append(profile)
    seen: set[str] = set()
    result: list[Profile] = []
    for profile in profiles:
        if profile.id.lower() in seen:
            continue
        seen.add(profile.id.lower())
        result.append(profile)
    return result


def load_profiles() -> tuple[list[Profile], str]:
    """加载内置机型 profile。

    返回 ``(profiles, error_message)``；``profiles`` 为空时 ``error_message`` 说明原因。
    绝不抛异常。
    """
    error = ""
    registry = _try_import("liveryforge.profiles.registry")
    if registry is None:
        error = "机型 registry 尚未就绪（liveryforge/profiles/registry.py 未找到）"
    if registry is not None:
        for name in ("list_profiles", "all_profiles", "get_profiles", "load_profiles", "profiles"):
            fn = getattr(registry, name, None)
            if not callable(fn):
                continue
            try:
                raw = fn()
            except Exception as exc:  # noqa: BLE001
                error = f"读取内置机型失败：{exc}"
                break
            if isinstance(raw, dict):
                raw = list(raw.values())
            if not isinstance(raw, (list, tuple)):
                error = "机型 registry 返回了无法识别的数据"
                break
            profiles = [p for p in (_normalize_profile(item) for item in raw) if p is not None]
            if profiles:
                return profiles, ""
            error = error or "内置机型列表为空"
            break
    fallback = _profiles_from_data_dir()
    if fallback:
        return fallback, ""
    return [], error or "暂无内置机型"


def get_profile(profile_id: str) -> Profile | None:
    if not profile_id:
        return None
    profiles, _ = load_profiles()
    for profile in profiles:
        if profile.id.lower() == profile_id.lower():
            return profile
    registry = _try_import("liveryforge.profiles.registry")
    if registry is not None:
        for name in ("get_profile", "load_profile", "find_profile"):
            fn = getattr(registry, name, None)
            if not callable(fn):
                continue
            try:
                profile = _normalize_profile(fn(profile_id))
            except Exception:  # noqa: BLE001
                profile = None
            if profile is not None:
                return profile
    return None


def format_profile_label(profile: Profile) -> str:
    generation = f" · {profile.generation}" if profile.generation else ""
    return f"{profile.name}（{profile.id}{generation}）"


# ---------------------------------------------------------------------------
# 贴图扫描 / 分类 / 转换
# ---------------------------------------------------------------------------
_KIND_PATTERNS: tuple[tuple[tuple[str, ...], TextureKind], ...] = (
    (("ALBD", "ALBEDO", "BASECOLOR", "BASE_COLOR", "DIFFUSE"), TextureKind.ALBEDO),
    # 注意：NORM 必须排在 COMP 之前——"NORM" 里包含 "ORM"，
    # 若先匹配 COMP 的 ORM 关键字，法线贴图会被误判成材质贴图。
    (("NORM", "NORMAL", "NRM"), TextureKind.NORMAL),
    (("COMP", "METALROUGH", "ORM", "MRAO"), TextureKind.COMP),
    (("EMIS", "EMISSIVE", "LIT"), TextureKind.EMISSIVE),
    (("DECAL", "DECALS"), TextureKind.DECAL),
)

_KIND_VALUE_MAP = {kind.value.lower(): kind for kind in TextureKind}
_KIND_VALUE_MAP.update({kind.name.lower(): kind for kind in TextureKind})


def _coerce_kind(value: Any) -> TextureKind | None:
    if isinstance(value, TextureKind):
        return value
    if value is None:
        return None
    if hasattr(value, "kind"):
        return _coerce_kind(getattr(value, "kind"))
    text = str(value).strip().lower()
    if text in _KIND_VALUE_MAP:
        return _KIND_VALUE_MAP[text]
    return None


def guess_kind_from_name(name: str) -> TextureKind:
    """文件名 -> 贴图类型（与 T1 classify 规则同源的降级实现）。"""
    stem = Path(name).stem.upper()
    for keys, kind in _KIND_PATTERNS:
        if any(key in stem for key in keys):
            return kind
    return TextureKind.GENERIC


def classify_texture(path: Path) -> TextureKind:
    """优先调用 T1 的 classify；不可用时使用内置文件名规则。"""
    classify_module = _try_import("liveryforge.texture.classify")
    if classify_module is not None:
        for func_name in (
            "classify_texture",
            "classify_file",
            "classify",
            "infer_kind",
            "detect_kind",
            "kind_for",
            "classify_path",
        ):
            func = getattr(classify_module, func_name, None)
            if not callable(func):
                continue
            for argument in (path, path.name):
                try:
                    kind = _coerce_kind(func(argument))
                except TypeError:
                    continue
                except Exception:  # noqa: BLE001
                    kind = None
                if kind is not None:
                    return kind
    return guess_kind_from_name(path.name)


@dataclass
class TextureEntry:
    """GUI 贴图列表中的一行。"""

    path: Path
    rel: str
    name: str
    kind: TextureKind
    width: int = 0
    height: int = 0
    size: int = 0
    error: str = ""

    @property
    def kind_label(self) -> str:
        return self.kind.label_zh

    @property
    def size_label(self) -> str:
        return human_size(self.size) if self.size else "—"

    @property
    def dimension_label(self) -> str:
        if self.width and self.height:
            return f"{self.width} × {self.height}"
        return "—"


def _read_image_size(path: Path) -> tuple[int, int, str]:
    try:
        from PIL import Image  # 懒加载：没装 Pillow 时仍能列出文件
    except Exception:  # noqa: BLE001
        return 0, 0, "缺少 Pillow，无法读取尺寸"
    try:
        with Image.open(path) as img:
            return int(img.width), int(img.height), ""
    except Exception as exc:  # noqa: BLE001 - 损坏图片/不支持的格式
        return 0, 0, f"无法读取图片：{exc}"


def _make_entry(path: Path, root: Path) -> TextureEntry:
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = path.name
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    width, height, error = _read_image_size(path)
    return TextureEntry(
        path=path,
        rel=rel,
        name=path.name,
        kind=classify_texture(path),
        width=width,
        height=height,
        size=size,
        error=error,
    )


def scan_texture_dir(directory: Path | str, recursive: bool = True) -> tuple[list[TextureEntry], list[str]]:
    """扫描目录中的 PNG/TIF 贴图，返回 ``(条目, 警告)``。

    * 默认递归扫描（paintkit 常带子目录）；
    * 同名文件（忽略大小写）只保留路径层级最浅的一个，并给出警告；
    * 读取尺寸失败不抛异常，记录在 ``entry.error``。
    """
    root = Path(directory)
    if not root.exists():
        raise LiveryForgeError(f"贴图目录不存在：{root}")
    if not root.is_dir():
        raise LiveryForgeError(f"贴图路径不是文件夹：{root}")

    candidates: list[Path] = []
    try:
        iterator: Iterable[Path] = root.rglob("*") if recursive else root.glob("*")
        for path in iterator:
            if path.is_file() and path.suffix.lower() in TEXTURE_EXTENSIONS:
                candidates.append(path)
    except OSError as exc:
        raise LiveryForgeError(f"读取贴图目录失败：{exc}") from exc

    candidates.sort(key=lambda p: (len(p.relative_to(root).parts), str(p).lower()))
    warnings: list[str] = []
    seen: dict[str, str] = {}
    entries: list[TextureEntry] = []
    for path in candidates:
        key = path.name.lower()
        if key in seen:
            warnings.append(f"同名贴图已跳过：{path.relative_to(root)}（使用 {seen[key]}）")
            continue
        seen[key] = str(path.relative_to(root))
        entries.append(_make_entry(path, root))
    return entries, warnings


def dimension_warnings(entries: Sequence[TextureEntry]) -> list[str]:
    """检查 2 的幂尺寸（MSFS 贴图强烈建议 2 的幂）。"""
    warnings: list[str] = []
    for entry in entries:
        if not (entry.width and entry.height):
            continue
        if not (is_power_of_two(entry.width) and is_power_of_two(entry.height)):
            warnings.append(
                f"{entry.name} 尺寸 {entry.width}×{entry.height} 不是 2 的幂，"
                "建议改为 512/1024/2048/4096 以避免 mipmap 异常"
            )
    return warnings


def _assets_from_entries(entries: Sequence[TextureEntry]) -> list[TextureAsset]:
    return [TextureAsset(source=entry.path, name=entry.path.name, kind=entry.kind) for entry in entries]


def _import_texture_pipeline():
    module = _try_import("liveryforge.texture.pipeline")
    convert = getattr(module, "convert_textures", None) if module is not None else None
    if not callable(convert):
        raise TextureError(
            "贴图转换引擎尚未就绪（liveryforge/texture/pipeline.py 未找到）。\n"
            "解决建议：确认使用的是完整安装包；若你是开发者，请先完成 T1 纹理引擎。"
        )
    return module


def convert_directory(
    texture_dir: Path | str,
    out_dir: Path | str,
    target_format: str = "ktx2",
    quality_high: bool = True,
    progress: ProgressCallback | None = None,
    recursive: bool = True,
) -> dict[str, Any]:
    """把目录中的贴图转换为 KTX2 / DDS / both。

    返回摘要 dict：``{"count", "textures", "elapsed", "out_dir", "warnings"}``。
    引擎未就绪时抛出中文 :class:`TextureError`。
    """
    pipeline = _import_texture_pipeline()
    entries, warnings = scan_texture_dir(texture_dir, recursive=recursive)
    if not entries:
        raise TextureError(
            "所选目录中没有找到 PNG/TIF 贴图。\n"
            "解决建议：选择包含 *.PNG / *.TIF 的文件夹（例如 paintkit 或 samples\\textures）。"
        )
    missing = [entry.name for entry in entries if entry.error and "缺少 Pillow" in entry.error]
    if missing:
        raise TextureError(
            "缺少 Pillow 组件，无法读取贴图尺寸。\n"
            "解决建议：使用官方便携运行时（run_gui.bat）启动，或重新安装完整版本。"
        )
    options = ConvertOptions(
        targets=TargetFormat(str(target_format).lower()),
        quality_high=bool(quality_high),
        mipmaps=True,
    )
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    started = time.time()
    results = pipeline.convert_textures(
        _assets_from_entries(entries), out_path, options, progress=progress
    )
    elapsed = time.time() - started
    return {
        "count": len(results),
        "textures": list(results),
        "elapsed": elapsed,
        "out_dir": out_path,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 涂装包构建 / 克隆
# ---------------------------------------------------------------------------
def _import_package_builder():
    module = _try_import("liveryforge.package.builder")
    build = getattr(module, "build_package", None) if module is not None else None
    if not callable(build):
        raise PackageError(
            "涂装包生成引擎尚未就绪（liveryforge/package/builder.py 未找到）。\n"
            "解决建议：可以先使用「贴图转换」页签只转换贴图；完整打包功能将在后续版本提供。"
        )
    return build


def _import_clone():
    module = _try_import("liveryforge.package.clone")
    clone = getattr(module, "clone_from_template", None) if module is not None else None
    if not callable(clone):
        raise PackageError(
            "克隆模式引擎尚未就绪（liveryforge/package/clone.py 未找到）。\n"
            "解决建议：改用「内置机型」模式，或使用「贴图转换」页签先转贴图。"
        )
    return clone


def make_build_request(
    profile: Profile,
    info: LiveryInfo,
    texture_dir: Path | str,
    output_root: Path | str,
    target_format: str = "ktx2",
    quality_high: bool = True,
    thumbnail_source: Path | str | None = None,
    copy_to_community: bool = False,
    community_dir: Path | str | None = None,
    overwrite: bool = True,
    author_slug: str = "",
) -> BuildRequest:
    thumbnail_path = Path(thumbnail_source) if thumbnail_source else None
    community_path = Path(community_dir) if community_dir else None
    return BuildRequest(
        profile=profile,
        info=info.normalized(),
        texture_dir=Path(texture_dir),
        output_root=Path(output_root),
        formats=TargetFormat(str(target_format).lower()),
        quality_high=bool(quality_high),
        thumbnail_source=thumbnail_path,
        include_json_sidecars=True,
        copy_to_community=bool(copy_to_community),
        community_dir=community_path,
        overwrite=bool(overwrite),
        author_slug=author_slug,
    )


def build_livery(
    profile: Profile,
    info: LiveryInfo,
    texture_dir: Path | str,
    output_root: Path | str,
    target_format: str = "ktx2",
    quality_high: bool = True,
    thumbnail_source: Path | str | None = None,
    copy_to_community: bool = False,
    community_dir: Path | str | None = None,
    overwrite: bool = True,
    progress: ProgressCallback | None = None,
) -> BuildResult:
    """内置机型模式：生成 2024 native 涂装包（或 2020 legacy 包）。"""
    build = _import_package_builder()
    request = make_build_request(
        profile=profile,
        info=info,
        texture_dir=texture_dir,
        output_root=output_root,
        target_format=target_format,
        quality_high=quality_high,
        thumbnail_source=thumbnail_source,
        copy_to_community=copy_to_community,
        community_dir=community_dir,
        overwrite=overwrite,
        author_slug=info.author,
    )
    return build(request, progress=progress)


def clone_livery(
    template_livery_dir: Path | str,
    info: LiveryInfo,
    texture_dir: Path | str,
    output_root: Path | str,
    target_format: str = "ktx2",
    quality_high: bool = True,
    thumbnail_source: Path | str | None = None,
    overwrite: bool = True,
    progress: ProgressCallback | None = None,
) -> BuildResult:
    """克隆模式：以已安装涂装文件夹为模板，替换贴图与配置后重新打包。"""
    template = Path(template_livery_dir)
    if not template.exists() or not template.is_dir():
        raise PackageError(
            f"模板涂装文件夹不存在：{template}\n"
            "解决建议：请选择 Community 中某个涂装包内的 livery 文件夹（含 livery.cfg）。"
        )
    clone = _import_clone()
    placeholder = Profile(
        id="clone",
        name=f"克隆：{template.name}",
        generation="2024",
        aircraft_path="",
        notes="由「克隆现有涂装」模式生成",
    )
    request = make_build_request(
        profile=placeholder,
        info=info,
        texture_dir=texture_dir,
        output_root=output_root,
        target_format=target_format,
        quality_high=quality_high,
        thumbnail_source=thumbnail_source,
        overwrite=overwrite,
        author_slug=info.author,
    )
    return clone(template, request, progress=progress)


# ---------------------------------------------------------------------------
# Community / 工具探测
# ---------------------------------------------------------------------------
_USERCFG_TARGETS = (
    ("Microsoft Flight Simulator 2024", "UserCfg.opt"),
    ("Microsoft Flight Simulator", "UserCfg.opt"),
)
_PACKAGES_RE = re.compile(r'InstalledPackagesPath\s+"([^"]+)"', re.IGNORECASE)


def _community_candidates_from_userconfig() -> list[Path]:
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return candidates
    roots: list[Path] = []
    for folder, filename in _USERCFG_TARGETS:
        cfg = Path(appdata) / folder / filename
        if not cfg.is_file():
            continue
        try:
            text = cfg.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for match in _PACKAGES_RE.finditer(text):
            roots.append(Path(match.group(1)))
    for root in roots:
        candidates.extend([root / "Community", root / "Community2024"])
    return candidates


def detect_community_dirs() -> list[Path]:
    """探测本机 Community 目录（返回存在的目录，按推荐顺序）。"""
    found: list[Path] = []

    def _add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _add(item)
            return
        if isinstance(value, dict):
            for key in ("community", "community_dir", "community2024", "path"):
                if key in value:
                    _add(value[key])
            return
        try:
            path = Path(str(value))
        except (TypeError, ValueError):
            return
        if path.is_dir():
            found.append(path)

    for env_name in ("MSFS_COMMUNITY_DIR", "MSFS2024_COMMUNITY_DIR", "MSFS_COMMUNITY"):
        _add(os.environ.get(env_name))

    simenv = _try_import("liveryforge.package.simenv")
    if simenv is not None:
        for func_name in (
            "detect_community_dirs",
            "find_community_dirs",
            "community_dirs",
            "detect_community",
            "find_community",
            "community_candidates",
            "detect_paths",
        ):
            func = getattr(simenv, func_name, None)
            if callable(func):
                try:
                    _add(func())
                except Exception:  # noqa: BLE001 - 引擎探测失败时继续用兜底方案
                    pass
                break

    _add(_community_candidates_from_userconfig())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def detect_community_dir() -> Path | None:
    dirs = detect_community_dirs()
    return dirs[0] if dirs else None


def texconv_status() -> tuple[bool, Path, str]:
    """返回 ``(是否存在, texconv 路径, 中文提示)``。"""
    path = texconv_path()
    if path.is_file():
        return True, path, "已找到内置 texconv.exe"
    return (
        False,
        path,
        f"未找到内置压缩工具 texconv.exe：{path}\n"
        "解决建议：1) 重新解压完整安装包（tools\\texconv 目录必须完整）；"
        "2) 若杀毒软件误报，请把项目目录加入白名单后重新解压。",
    )


def pillow_status() -> tuple[bool, str]:
    try:
        import PIL  # noqa: F401

        version = getattr(PIL, "__version__", "")
        return True, f"Pillow {version} 已就绪"
    except Exception:  # noqa: BLE001
        return False, "缺少 Pillow：无法读取贴图尺寸/生成缩略图，请使用官方便携运行时"


def engine_status() -> dict[str, dict[str, Any]]:
    """各引擎模块可用性，供 GUI 状态栏与 CLI detect 使用。"""
    profiles, profile_error = load_profiles()
    texconv_ok, texconv_file, texconv_hint = texconv_status()
    pillow_ok, pillow_hint = pillow_status()
    texture_ready = _module_available("liveryforge.texture.pipeline")
    builder_ready = _module_available("liveryforge.package.builder")
    clone_ready = _module_available("liveryforge.package.clone")
    return {
        "profiles": {
            "ready": bool(profiles),
            "label": "内置机型",
            "detail": f"{len(profiles)} 个" if profiles else "暂无内置机型，可用克隆模式",
            "hint": profile_error,
        },
        "texture": {
            "ready": texture_ready,
            "label": "贴图转换引擎",
            "detail": "已就绪" if texture_ready else "尚未就绪",
            "hint": "liveryforge/texture/pipeline.py 未找到，暂时只能浏览贴图",
        },
        "builder": {
            "ready": builder_ready,
            "label": "打包引擎",
            "detail": "已就绪" if builder_ready else "尚未就绪",
            "hint": "liveryforge/package/builder.py 未找到，暂时无法生成涂装包",
        },
        "clone": {
            "ready": clone_ready,
            "label": "克隆引擎",
            "detail": "已就绪" if clone_ready else "尚未就绪",
            "hint": "liveryforge/package/clone.py 未找到，暂时无法克隆现有涂装",
        },
        "texconv": {
            "ready": texconv_ok,
            "label": "texconv",
            "detail": str(texconv_file) if texconv_ok else "缺失",
            "hint": texconv_hint,
        },
        "pillow": {
            "ready": pillow_ok,
            "label": "Pillow",
            "detail": pillow_hint,
            "hint": pillow_hint,
        },
    }


# ---------------------------------------------------------------------------
# 文件系统辅助（GUI 用）
# ---------------------------------------------------------------------------
def open_directory(path: Path | str) -> bool:
    """用资源管理器打开目录；失败返回 False。"""
    target = Path(path)
    if not target.exists():
        return False
    try:
        os.startfile(str(target))  # type: ignore[attr-defined] - Windows
        return True
    except (AttributeError, OSError):
        return False


def copy_package_to_community(
    package_dir: Path | str,
    community_dir: Path | str,
    progress: Callable[[str, float | None], None] | None = None,
) -> dict[str, Any]:
    """把生成好的涂装包复制到 Community（整包目录复制）。"""
    source = Path(package_dir)
    community = Path(community_dir)
    if not source.is_dir():
        raise PackageError(f"涂装包目录不存在：{source}")
    if not community.is_dir():
        raise PackageError(
            f"Community 目录不存在：{community}\n"
            "解决建议：在「设置」页签点击「自动探测」或手动选择正确的 Community 文件夹。"
        )
    destination = community / source.name
    if progress:
        progress(f"正在复制到 Community：{destination}", None)
    file_count = 0
    total_bytes = 0
    try:
        for root, _dirs, files in os.walk(source):
            for filename in files:
                src_file = Path(root) / filename
                rel = src_file.relative_to(source)
                dst_file = destination / rel
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                file_count += 1
                total_bytes += src_file.stat().st_size
    except OSError as exc:
        raise PackageError(f"复制到 Community 失败：{exc}") from exc
    return {
        "destination": destination,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "message": f"已复制 {file_count} 个文件（{human_size(total_bytes)}）到 {destination}",
    }


def open_samples_dir() -> Path:
    return SAMPLES_DIR


def project_root() -> Path:
    return PROJECT_ROOT


__all__ = [
    "TEXTURE_EXTENSIONS",
    "TextureEntry",
    "build_livery",
    "classify_texture",
    "clone_livery",
    "convert_directory",
    "copy_package_to_community",
    "default_output_dir",
    "detect_community_dir",
    "detect_community_dirs",
    "dimension_warnings",
    "engine_status",
    "format_profile_label",
    "get_profile",
    "guess_kind_from_name",
    "human_size",
    "is_power_of_two",
    "load_profiles",
    "make_build_request",
    "open_directory",
    "open_samples_dir",
    "pillow_status",
    "project_root",
    "scan_texture_dir",
    "texconv_status",
    "user_config_dir",
]
