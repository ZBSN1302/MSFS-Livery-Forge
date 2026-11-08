"""涂装包构建：2024 native 包与 2020 legacy 包。

契约见 ARCHITECTURE.md §6：

2024 native::

    <output_root>/<package_name>/
      manifest.json
      layout.json
      SimObjects/Airplanes/<aircraft_path>/liveries/<creator>/<livery_name>/
        livery.cfg
        texture/ <*.PNG.KTX2> <*.PNG.KTX2.json> [texture.cfg]
        thumbnail/ thumbnail.png thumbnail_small.png [side/button/popup]

2020 legacy::

    <output_root>/<package_name>/
      manifest.json
      layout.json
      SimObjects/AirPlanes/<variant>-<CODE>/
        aircraft.cfg
        TEXTURE.<CODE>/*.PNG.DDS *.PNG.DDS.json texture.CFG thumbnail*.jpg

纹理由纹理引擎 ``liveryforge.texture.pipeline.convert_textures`` 产出；本模块
**不自行实现** KTX2/DDS 编码。导入延迟到运行时，纹理引擎缺失时抛中文
:class:`PackageError`（测试可用 monkeypatch 替换 ``_load_convert_textures``）。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from liveryforge import utils
from liveryforge.types import (
    BuildRequest,
    BuildResult,
    ConvertOptions,
    ConvertedTexture,
    LiveryForgeError,
    PackageError,
    Profile,
    TargetFormat,
    TextureAsset,
    TextureKind,
)
from liveryforge.package import layout as layout_mod
from liveryforge.package import liverycfg
from liveryforge.package import manifest as manifest_mod
from liveryforge.package import thumbnails

_TEXTURE_SUFFIXES = {".png", ".tif", ".tiff"}
_KTX2_SUFFIX = ".KTX2"
_DDS_SUFFIX = ".DDS"


# --------------------------------------------------------------------------
# 进度 / 日志
# --------------------------------------------------------------------------
def _emit(progress, message: str, fraction: float | None = None) -> None:
    if progress is None:
        return
    try:
        progress(message, fraction)
    except Exception:
        # 进度回调异常不应中断构建
        pass


# --------------------------------------------------------------------------
# 纹理引擎（延迟导入）
# --------------------------------------------------------------------------
def _load_convert_textures():
    """延迟导入纹理引擎入口；缺失时抛出可读中文错误。"""
    try:
        from liveryforge.texture.pipeline import convert_textures  # noqa: WPS433
    except Exception as exc:  # ImportError 或模块内部导入失败
        raise PackageError(
            "纹理引擎不可用：无法导入 liveryforge.texture.pipeline.convert_textures"
            f"（{exc}）。请确认纹理引擎已就绪后再构建涂装包。") from exc
    if not callable(convert_textures):
        raise PackageError("纹理引擎接口异常：convert_textures 不可调用。")
    return convert_textures


# --------------------------------------------------------------------------
# 文件名 / 命名规则
# --------------------------------------------------------------------------
def slugify(text: str, fallback: str = "livery") -> str:
    """转成适合包名的 slug：小写、空白/下划线转连字符、去掉非法字符。"""
    cleaned = utils.sanitize_filename(str(text), fallback=fallback).strip().lower()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    return cleaned or fallback


def make_package_name(req: BuildRequest) -> str:
    """包名规则：``<creator>-livery-<slug>``（slug 优先用注册号）。"""
    creator = req.author_slug or req.info.author or req.profile.creator_folder or "liveryforge"
    creator = slugify(creator, "liveryforge")
    slug_source = req.info.atc_id or req.info.title or req.profile.id
    return f"{creator}-livery-{slugify(slug_source, 'livery')}"


def livery_dir_name(req: BuildRequest) -> str:
    """涂装文件夹名：title 清理后原名（保留空格/大小写，Windows 合法）。"""
    return utils.sanitize_filename(req.info.title, fallback="livery")


def texture_code(info) -> str:
    """2020 legacy 纹理代码（TEXTURE.<CODE> 的 CODE），如 CHH。"""
    raw = info.icao_airline or info.atc_airline or info.atc_id or info.title
    code = re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()
    return code[:12] or "LIV"


def legacy_variant_path(profile: Profile, code: str) -> str:
    """2020 legacy 变体目录：``<aircraft_path>-<CODE>``（真实 FBW A320 风格）。"""
    base = str(profile.aircraft_path or "SimObjects/AirPlanes/FlyByWire_A320_NEO")
    base = base.replace("\\", "/").rstrip("/")
    return f"{base}-{code}"


# --------------------------------------------------------------------------
# 贴图收集与分类
# --------------------------------------------------------------------------
def _infer_kind(name: str) -> TextureKind:
    stem = Path(name).stem.upper()
    tokens = re.split(r"[^A-Z0-9]+", stem)
    token_set = set(tokens)
    if "NORM" in token_set or "NORMAL" in token_set:
        return TextureKind.NORMAL
    if token_set & {"COMP", "COMPLETE", "MR", "METALROUGH"} or "COMP" in stem:
        return TextureKind.COMP
    if token_set & {"ALBD", "ALBEDO", "BASE", "DIFFUSE"} or "ALBD" in stem:
        return TextureKind.ALBEDO
    if token_set & {"DECAL", "DECALS"} or "DECAL" in stem:
        return TextureKind.DECAL
    if token_set & {"EMIS", "EMISSIVE"} or "EMIS" in stem:
        return TextureKind.EMISSIVE
    return TextureKind.GENERIC


def _classify_file(path: Path) -> TextureKind:
    """优先用 T1 的 classify（若存在），失败回退到本模块简单后缀规则。"""
    try:
        from liveryforge.texture import classify as classify_mod  # noqa: WPS433
    except Exception:
        classify_mod = None
    if classify_mod is not None:
        for attr in ("classify_file", "classify_path", "infer_kind", "classify"):
            func = getattr(classify_mod, attr, None)
            if not callable(func):
                continue
            try:
                result = func(path)
            except TypeError:
                try:
                    result = func(path.name)
                except Exception:
                    break
            except Exception:
                break
            if isinstance(result, TextureKind):
                return result
            if isinstance(result, str):
                try:
                    return TextureKind(result)
                except ValueError:
                    return _infer_kind(path.name)
            if result is not None:
                return _infer_kind(path.name)
    return _infer_kind(path.name)


def collect_assets(texture_dir: Path) -> list[TextureAsset]:
    """扫描贴图目录顶层的 PNG/TIF，返回 TextureAsset 列表（按文件名排序）。"""
    texture_dir = Path(texture_dir)
    if not texture_dir.is_dir():
        raise PackageError(f"贴图目录不存在：{texture_dir}")
    files = [p for p in sorted(texture_dir.iterdir(), key=lambda p: p.name.casefold())
             if p.is_file() and p.suffix.lower() in _TEXTURE_SUFFIXES]
    if not files:
        raise PackageError(
            f"贴图目录中没有 PNG/TIF 文件：{texture_dir}。"
            "请把需要转换的贴图放到该目录（文件名如 A320_FUSE_ALBD.PNG）。")
    return [TextureAsset(source=p, name=p.name, kind=_classify_file(p)) for p in files]


# --------------------------------------------------------------------------
# 纹理转换与落盘
# --------------------------------------------------------------------------
def convert_assets(assets: list[TextureAsset],
                   out_dir: Path,
                   *,
                   targets: TargetFormat,
                   quality_high: bool = True,
                   progress=None) -> list[ConvertedTexture]:
    """调用纹理引擎转换，返回 ConvertedTexture 列表。"""
    convert_textures = _load_convert_textures()
    options = ConvertOptions(targets=targets, quality_high=quality_high)
    try:
        converted = convert_textures(assets, out_dir, options, progress=progress)
    except LiveryForgeError:
        raise
    except Exception as exc:
        raise PackageError(f"纹理解析/转换失败：{exc}") from exc
    if converted is None:
        raise PackageError("纹理引擎返回空结果（未生成任何纹理）。")
    return list(converted)


def _output_by_suffix(converted: ConvertedTexture, suffix: str) -> Path | None:
    for value in (converted.outputs or {}).values():
        if value and str(value).lower().endswith(suffix.lower()):
            return Path(value)
    return None


def _move_named(output: Path, target: Path) -> Path:
    """把转换输出重命名/移动到契约要求的文件名。"""
    output = Path(output)
    target = Path(target)
    if output.resolve() == target.resolve():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.resolve() == target.parent.resolve():
        output.replace(target)
    else:
        shutil.move(str(output), str(target))
    return target


def _place_2024_textures(converted: list[ConvertedTexture], texture_dir: Path,
                         include_json: bool) -> tuple[list[Path], list[str]]:
    warnings: list[str] = []
    written: list[Path] = []
    for item in converted:
        asset_name = item.asset.name
        main = (item.outputs or {}).get("ktx2") or _output_by_suffix(item, ".KTX2")
        if not main:
            warnings.append(f"纹理未生成 KTX2：{asset_name}")
            continue
        target = texture_dir / f"{asset_name}{_KTX2_SUFFIX}"
        written.append(_move_named(Path(main), target))
        sidecar = (item.outputs or {}).get("ktx2_json")
        if include_json:
            if sidecar and Path(sidecar).exists():
                _move_named(Path(sidecar), target.with_name(target.name + ".json"))
            elif target.with_name(target.name + ".json").exists():
                pass
            else:
                warnings.append(f"纹理缺少 .KTX2.json 元数据：{asset_name}")
        elif sidecar and Path(sidecar).exists():
            Path(sidecar).unlink(missing_ok=True)
    return written, warnings


def _place_2020_textures(converted: list[ConvertedTexture], texture_dir: Path,
                         include_json: bool) -> tuple[list[Path], list[str]]:
    warnings: list[str] = []
    written: list[Path] = []
    for item in converted:
        asset_name = item.asset.name
        main = (item.outputs or {}).get("dds") or _output_by_suffix(item, ".DDS")
        if not main:
            warnings.append(f"纹理未生成 DDS：{asset_name}")
            continue
        target = texture_dir / f"{asset_name}{_DDS_SUFFIX}"
        written.append(_move_named(Path(main), target))
        sidecar = (item.outputs or {}).get("dds_json")
        if include_json:
            if sidecar and Path(sidecar).exists():
                _move_named(Path(sidecar), target.with_name(target.name + ".json"))
        elif sidecar and Path(sidecar).exists():
            Path(sidecar).unlink(missing_ok=True)
    return written, warnings


# --------------------------------------------------------------------------
# 通用辅助
# --------------------------------------------------------------------------
def _check_title(req: BuildRequest) -> None:
    if not str(req.info.title or "").strip():
        raise PackageError("涂装标题（title）不能为空。")


def _prepare_package_dir(package_dir: Path, overwrite: bool, log: list[str]) -> None:
    if package_dir.exists():
        if not overwrite:
            raise PackageError(
                f"输出包已存在：{package_dir}。请勾选“覆盖已存在的包”或更换输出目录。")
        shutil.rmtree(package_dir)
        log.append(f"已覆盖旧包目录：{package_dir}")
    package_dir.mkdir(parents=True, exist_ok=True)


def _package_stats(package_dir: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for path in Path(package_dir).rglob("*"):
        if path.is_file():
            file_count += 1
            total_bytes += path.stat().st_size
    return file_count, total_bytes


def _profile_extras(profile: Profile) -> dict:
    try:
        from liveryforge.profiles.registry import get_profile_extra
        return get_profile_extra(profile.id) or {}
    except Exception:
        return {}


def _write_package_files(package_dir: Path, req: BuildRequest,
                         log: list[str], progress=None) -> tuple[Path, dict, Path]:
    """写 layout.json 与 manifest.json，返回 (layout_path, manifest, manifest_path)。"""
    _emit(progress, "生成 layout.json", 0.92)
    layout_path = layout_mod.write_layout(package_dir)
    log.append(f"layout.json：{layout_path.name}（{len(layout_mod.generate_layout(package_dir)['content'])} 个条目）")

    extras = _profile_extras(req.profile)
    _emit(progress, "生成 manifest.json", 0.96)
    manifest_path, manifest = manifest_mod.write_manifest(
        package_dir, req.profile, req.info,
        manufacturer=extras.get("manufacturer"),
        content_type=req.profile.content_type,
        package_version=req.info.version,
        minimum_game_version=extras.get("minimum_game_version"),
        minimum_compatibility_version=extras.get("minimum_compatibility_version"),
        package_order_hint=req.profile.package_order_hint,
    )
    log.append(f"manifest.json：total_package_size={manifest['total_package_size']}")
    return layout_path, manifest, manifest_path


def _finish(package_dir: Path, livery_dir: Path, manifest_path: Path,
            layout_path: Path, textures: list[ConvertedTexture],
            warnings: list[str], log: list[str], package_name: str,
            progress=None) -> BuildResult:
    file_count, total_bytes = _package_stats(package_dir)
    result = BuildResult(
        package_dir=package_dir,
        livery_dir=livery_dir,
        manifest_path=manifest_path,
        layout_path=layout_path,
        textures=textures,
        warnings=warnings,
        log=log,
        file_count=file_count,
        total_bytes=total_bytes,
        package_name=package_name,
    )
    _emit(progress, f"构建完成：{package_name}（{file_count} 个文件，{total_bytes} 字节）", 1.0)
    return result


def _copy_to_community(result: BuildResult, req: BuildRequest) -> None:
    if not req.copy_to_community:
        return
    if not req.community_dir:
        raise PackageError("已勾选“复制到 Community”，但未指定 Community 目录。")
    community_dir = Path(req.community_dir)
    if not community_dir.is_dir():
        raise PackageError(f"Community 目录不存在：{community_dir}")
    target = community_dir / result.package_name
    if target.exists():
        if not req.overwrite:
            raise PackageError(
                f"Community 中已存在同名包：{target}。请勾选覆盖或先手动删除。")
        shutil.rmtree(target)
    shutil.copytree(result.package_dir, target)
    result.community_copied = True
    result.log.append(f"已复制到 Community：{target}")


# --------------------------------------------------------------------------
# 2024 native 包
# --------------------------------------------------------------------------
def _build_2024(req: BuildRequest, package_dir: Path, progress=None) -> BuildResult:
    profile = req.profile
    info = req.info
    log: list[str] = []
    warnings: list[str] = []
    _emit(progress, "准备目录", 0.02)

    aircraft_rel = str(profile.aircraft_path or "SimObjects/Airplanes/asobo_longitude")
    aircraft_rel = aircraft_rel.replace("\\", "/").strip("/")
    creator = utils.sanitize_filename(profile.creator_folder or "liveryforge") or "liveryforge"
    livery_dir = package_dir / aircraft_rel / "liveries" / creator / livery_dir_name(req)
    texture_dir = livery_dir / "texture"
    thumbnail_dir = livery_dir / "thumbnail"
    for directory in (livery_dir, texture_dir, thumbnail_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _emit(progress, "写入 livery.cfg", 0.05)
    liverycfg.write_livery_cfg(livery_dir / liverycfg.LIVERY_CFG, profile, info)
    log.append(f"livery.cfg：{aircraft_rel}/liveries/{creator}/{livery_dir.name}")

    assets = collect_assets(req.texture_dir)
    log.append("识别贴图：" + "、".join(asset.name for asset in assets))

    targets = req.formats
    if targets not in (TargetFormat.KTX2, TargetFormat.BOTH):
        warnings.append(f"2024 包必须使用 KTX2，已忽略请求格式 {targets.value}。")
        targets = TargetFormat.KTX2
    _emit(progress, f"转换贴图（{len(assets)} 个，KTX2）", 0.1)
    converted = convert_assets(assets, texture_dir, targets=targets,
                               quality_high=req.quality_high, progress=progress)
    placed, place_warnings = _place_2024_textures(
        converted, texture_dir, req.include_json_sidecars)
    warnings.extend(place_warnings)
    log.append(f"已写入 KTX2 纹理 {len(placed)} 个")

    if profile.texture_cfg_fallbacks:
        _emit(progress, "写入 texture.cfg", 0.8)
        liverycfg.write_texture_cfg(texture_dir / liverycfg.TEXTURE_CFG,
                                    profile.texture_cfg_fallbacks, generation="2024")
        log.append("texture.cfg：fallback 列表已写入")

    _emit(progress, "生成缩略图", 0.84)
    sizes = thumbnails.thumbnail_sizes_for("2024", profile.thumbnail_sizes)
    written_thumbs = thumbnails.generate_thumbnails(
        req.thumbnail_source, thumbnail_dir, title=info.title,
        sizes=sizes, generation="2024", progress=progress)
    log.append("thumbnail：" + "、".join(p.name for p in written_thumbs))

    layout_path, _manifest, manifest_path = _write_package_files(
        package_dir, req, log, progress)
    return _finish(package_dir, livery_dir, manifest_path, layout_path,
                   converted, warnings, log, package_dir.name, progress)


# --------------------------------------------------------------------------
# 2020 legacy 包
# --------------------------------------------------------------------------
def _build_2020(req: BuildRequest, package_dir: Path, progress=None) -> BuildResult:
    profile = req.profile
    info = req.info
    log: list[str] = []
    warnings: list[str] = []
    _emit(progress, "准备目录", 0.02)

    code = texture_code(info)
    variant_rel = legacy_variant_path(profile, code)
    variant_dir = package_dir / variant_rel
    texture_dir = variant_dir / f"TEXTURE.{code}"
    for directory in (variant_dir, texture_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _emit(progress, "写入 aircraft.cfg", 0.05)
    liverycfg.write_aircraft_cfg(variant_dir / liverycfg.AIRCRAFT_CFG,
                                 profile, info, code)
    log.append(f"aircraft.cfg：{variant_rel}（texture={code}）")

    assets = collect_assets(req.texture_dir)
    log.append("识别贴图：" + "、".join(asset.name for asset in assets))
    if req.formats not in (TargetFormat.DDS, TargetFormat.BOTH):
        warnings.append(f"2020 legacy 包必须使用 DDS，已忽略请求格式 {req.formats.value}。")
    _emit(progress, f"转换贴图（{len(assets)} 个，DDS）", 0.1)
    converted = convert_assets(assets, texture_dir, targets=TargetFormat.DDS,
                               quality_high=req.quality_high, progress=progress)
    placed, place_warnings = _place_2020_textures(
        converted, texture_dir, req.include_json_sidecars)
    warnings.extend(place_warnings)
    log.append(f"已写入 DDS 纹理 {len(placed)} 个")

    fallbacks = profile.texture_cfg_fallbacks or []
    if fallbacks:
        _emit(progress, "写入 texture.CFG", 0.8)
        liverycfg.write_texture_cfg(texture_dir / "texture.CFG",
                                    fallbacks, generation="2020")
        log.append("texture.CFG：fallback 列表已写入")
    else:
        warnings.append("当前机型 profile 未配置 texture.CFG fallback，已跳过该文件。")

    _emit(progress, "生成缩略图", 0.84)
    sizes = thumbnails.thumbnail_sizes_for("2020", profile.thumbnail_sizes)
    written_thumbs = thumbnails.generate_thumbnails(
        req.thumbnail_source, texture_dir, title=info.title,
        sizes=sizes, generation="2020", progress=progress)
    log.append("thumbnail：" + "、".join(p.name for p in written_thumbs))

    layout_path, _manifest, manifest_path = _write_package_files(
        package_dir, req, log, progress)
    return _finish(package_dir, variant_dir, manifest_path, layout_path,
                   converted, warnings, log, package_dir.name, progress)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def build_package(req: BuildRequest,
                  progress=None) -> BuildResult:
    """按 BuildRequest 生成完整涂装包，返回 BuildResult。"""
    _check_title(req)
    generation = str(req.profile.generation or "2024")
    if generation not in ("2024", "2020"):
        raise PackageError(f"不支持的机型代际：{generation}（应为 2024 或 2020）。")
    output_root = Path(req.output_root)
    if not str(output_root):
        raise PackageError("未指定输出目录。")
    output_root.mkdir(parents=True, exist_ok=True)

    package_name = make_package_name(req)
    package_dir = output_root / package_name
    log: list[str] = [f"包名：{package_name}"]
    _prepare_package_dir(package_dir, req.overwrite, log)

    _emit(progress, f"开始构建：{package_name}", 0.0)
    if generation == "2020":
        result = _build_2020(req, package_dir, progress)
    else:
        result = _build_2024(req, package_dir, progress)
    result.log[:0] = log
    _copy_to_community(result, req)
    return result


__all__ = [
    "build_package",
    "collect_assets",
    "convert_assets",
    "legacy_variant_path",
    "livery_dir_name",
    "make_package_name",
    "slugify",
    "texture_code",
]
