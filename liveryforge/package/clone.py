"""克隆现有涂装：复制模板完整结构，用用户贴图替换同名纹理，重新打包。

流程（ARCHITECTURE §6 克隆模式）：
1. 识别模板包根（向上找 ``manifest.json`` / ``layout.json``）与 livery 文件夹；
2. 复制模板 livery 文件夹全部文件（含 model.* / panel.* / texture.cfg / thumbnail 等）；
3. 用户贴图按基名（忽略大小写，去掉 ``.PNG`` / ``.TIF`` 扩展层）匹配模板
   ``<基名>.PNG.KTX2`` / ``<基名>.TIF.KTX2`` 并用转换结果替换；未匹配的模板纹理
   原样保留，未匹配的用户贴图作为新增纹理写入；
4. 更新 livery.cfg 的 name/atc_id/atc_airline/icao_airline（保留其它节与键），
   更新 manifest 的 title/creator/package_version；
5. 重新生成 layout.json / manifest.json / 缩略图；
6. ``BuildResult.warnings`` 记录替换、保留、未匹配清单。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from liveryforge import utils
from liveryforge.package import builder
from liveryforge.package import layout as layout_mod
from liveryforge.package import liverycfg
from liveryforge.package import manifest as manifest_mod
from liveryforge.package import simenv
from liveryforge.package import thumbnails
from liveryforge.types import BuildRequest, BuildResult, PackageError, TargetFormat

_TEMPLATE_TEXTURE_SUFFIXES = (".ktx2", ".dds")


# --------------------------------------------------------------------------
# 模板定位
# --------------------------------------------------------------------------
def _find_package_root(start: Path) -> Path | None:
    """从 livery 目录向上找含 manifest.json / layout.json 的包根。"""
    current = Path(start).resolve()
    for parent in (current, *current.parents):
        if (parent / "manifest.json").is_file() or (parent / "layout.json").is_file():
            return parent
    return None


def _relative_or_none(path: Path, root: Path | None) -> Path | None:
    if root is None:
        return None
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _find_variant_texture_dir(variant_dir: Path) -> Path | None:
    try:
        for child in variant_dir.iterdir():
            if child.is_dir() and child.name.lower().startswith("texture."):
                return child
    except OSError:
        return None
    return None


def _list_template_textures(texture_dir: Path) -> list[Path]:
    """列出模板中的主纹理文件（*.KTX2 / *.DDS，排除 .json/.FLAGS）。"""
    results: list[Path] = []
    if texture_dir is None or not texture_dir.is_dir():
        return results
    for path in texture_dir.rglob("*"):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if lower.endswith(".json"):
            continue
        if lower.endswith(_TEMPLATE_TEXTURE_SUFFIXES):
            results.append(path)
    results.sort(key=lambda p: str(p).casefold())
    return results


def texture_basename(name: str) -> str:
    """取纹理基名：去掉 .KTX2/.DDS 与一层 .PNG/.TIF 扩展，统一小写。"""
    lower = str(name).lower()
    for suffix in (".ktx2", ".dds"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
            break
    for suffix in (".png", ".tif", ".tiff"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
            break
    return lower


# --------------------------------------------------------------------------
# 复制与替换
# --------------------------------------------------------------------------
def _copy_livery_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _make_temp_dir(parent: Path) -> Path:
    """在指定目录下创建唯一临时目录（不用 tempfile.mkdtemp：其 0700 权限在
    受限沙箱下可能导致后续写入被拒绝）。"""
    path = Path(parent) / f".liveryforge_clone_{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _replace_with_converted(converted, template_path: Path,
                            *, suffix: str) -> None:
    """把转换结果写到模板文件名（保留模板命名），并写对应 json sidecar。"""
    main = (converted.outputs or {}).get(
        "ktx2" if suffix == ".KTX2" else "dds")
    if main is None:
        for value in (converted.outputs or {}).values():
            if value and str(value).lower().endswith(suffix.lower()):
                main = value
                break
    if main is None:
        raise PackageError(f"纹理未生成 {suffix}：{converted.asset.name}")
    shutil.copy2(str(main), str(template_path))
    sidecar_key = "ktx2_json" if suffix == ".KTX2" else "dds_json"
    sidecar = (converted.outputs or {}).get(sidecar_key)
    if sidecar and Path(sidecar).is_file():
        shutil.copy2(str(sidecar), str(template_path) + ".json")


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def clone_from_template(template_livery_dir: Path,
                        req: BuildRequest,
                        progress=None) -> BuildResult:
    """克隆模板涂装并用用户贴图替换，返回 BuildResult。"""
    builder._check_title(req)
    try:
        livery_dir, generation = simenv.find_livery_dir(Path(template_livery_dir))
    except simenv.DiscoveryError as exc:
        raise PackageError(str(exc)) from exc

    package_root = _find_package_root(livery_dir)
    output_root = Path(req.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    package_name = builder.make_package_name(req)
    package_dir = output_root / package_name

    log: list[str] = [f"包名：{package_name}", f"克隆模板：{livery_dir}", f"模板代际：{generation}"]
    builder._prepare_package_dir(package_dir, req.overwrite, log)
    builder._emit(progress, f"开始克隆：{livery_dir.name}", 0.0)

    warnings: list[str] = []
    if package_root is not None:
        try:
            extras = [p.name for p in package_root.iterdir()
                      if p.is_dir() and p.name.lower() not in ("simobjects", "liveries")]
        except OSError:
            extras = []
        if extras:
            warnings.append(
                "模板包根含非涂装内容（" + "、".join(extras) + "），克隆仅复制涂装文件夹，"
                "如该内容为机模依赖请手动补齐。")

    if generation == "2020":
        result = _clone_legacy(livery_dir, package_root, package_dir, req,
                               warnings, log, progress)
    else:
        result = _clone_2024(livery_dir, package_root, package_dir, req,
                             warnings, log, progress)
    result.log[:0] = log
    builder._copy_to_community(result, req)
    return result


def _clone_2024(template_dir: Path, package_root: Path | None, package_dir: Path,
                req: BuildRequest, warnings: list[str], log: list[str],
                progress=None) -> BuildResult:
    aircraft_dir = template_dir.parent.parent.parent
    aircraft_rel = _relative_or_none(aircraft_dir, package_root)
    if aircraft_rel is None:
        aircraft_rel = Path(str(req.profile.aircraft_path or
                                "SimObjects/Airplanes/asobo_longitude").replace("\\", "/"))
    creator = utils.sanitize_filename(
        req.profile.creator_folder or template_dir.parent.name or "liveryforge")
    new_livery_dir = package_dir / aircraft_rel / "liveries" / creator / builder.livery_dir_name(req)

    builder._emit(progress, "复制模板涂装文件", 0.1)
    _copy_livery_tree(template_dir, new_livery_dir)
    log.append(f"已复制模板文件到 {new_livery_dir.relative_to(package_dir)}")

    texture_dir = new_livery_dir / "texture"
    texture_dir.mkdir(parents=True, exist_ok=True)

    # 用户贴图转换
    assets = builder.collect_assets(req.texture_dir)
    log.append("识别用户贴图：" + "、".join(a.name for a in assets))
    temp_dir = _make_temp_dir(package_dir.parent)
    try:
        builder._emit(progress, f"转换贴图（{len(assets)} 个，KTX2）", 0.25)
        converted = builder.convert_assets(assets, temp_dir,
                                           targets=TargetFormat.KTX2,
                                           quality_high=req.quality_high,
                                           progress=progress)
        _apply_texture_replacements(converted, texture_dir, warnings,
                                    log, suffix=".KTX2")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # 更新 livery.cfg（保留其它节/键）
    cfg_path = new_livery_dir / liverycfg.LIVERY_CFG
    if cfg_path.is_file():
        old_text = cfg_path.read_text(encoding="utf-8-sig", errors="ignore")
        new_text = liverycfg.update_livery_cfg(old_text, req.info)
        utils.atomic_write_bytes(cfg_path, new_text.encode("utf-8"))
        log.append("已更新 livery.cfg 的 name/atc 字段")
    else:
        liverycfg.write_livery_cfg(cfg_path, req.profile, req.info)
        warnings.append("模板缺少 livery.cfg，已按机型 profile 新建。")

    # texture.cfg：模板已有则保留；没有且 profile 配置了 fallback 才写
    texture_cfg_path = texture_dir / liverycfg.TEXTURE_CFG
    if not texture_cfg_path.exists() and req.profile.texture_cfg_fallbacks:
        liverycfg.write_texture_cfg(texture_cfg_path,
                                    req.profile.texture_cfg_fallbacks, generation="2024")
        log.append("已按 profile 写入 texture.cfg")

    # 缩略图重新生成（先清空模板旧缩略图，避免旧涂装图片残留）
    builder._emit(progress, "重新生成缩略图", 0.8)
    thumb_dir = new_livery_dir / "thumbnail"
    template_thumbs = thumbnails.template_thumbnail_sizes(thumb_dir if thumb_dir.exists() else template_dir / "thumbnail")
    if thumb_dir.exists():
        shutil.rmtree(thumb_dir)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    sizes = thumbnails.thumbnail_sizes_for("2024", req.profile.thumbnail_sizes)
    # 保留模板里实际存在的缩略图文件名（例如 A380 用 thumbnail-small.png），
    # 避免克隆后从 5 张缩略图退化成 2 张。
    sizes.update(template_thumbs)
    # 若模板用连字符命名，删掉默认的下划线同名项，避免同一尺寸生成两份不同名文件
    for hyphen_name in list(template_thumbs):
        underscore = hyphen_name.replace("-", "_")
        if underscore != hyphen_name and underscore in sizes and hyphen_name not in (req.profile.thumbnail_sizes or {}):
            sizes.pop(underscore, None)
    written = thumbnails.generate_thumbnails(
        req.thumbnail_source, thumb_dir, title=req.info.title,
        sizes=sizes, generation="2024", progress=progress)
    log.append("thumbnail：" + "、".join(p.name for p in written))

    layout_path, _manifest, manifest_path = builder._write_package_files(
        package_dir, req, log, progress)
    result = builder._finish(package_dir, new_livery_dir, manifest_path, layout_path,
                             converted, warnings, log, package_name=package_dir.name,
                             progress=progress)
    return result


def _clone_legacy(template_dir: Path, package_root: Path | None, package_dir: Path,
                  req: BuildRequest, warnings: list[str], log: list[str],
                  progress=None) -> BuildResult:
    rel_variant = _relative_or_none(template_dir, package_root)
    if rel_variant is None:
        rel_variant = Path("SimObjects/AirPlanes") / template_dir.name
    new_variant_dir = package_dir / rel_variant

    builder._emit(progress, "复制模板涂装文件", 0.1)
    _copy_livery_tree(template_dir, new_variant_dir)
    log.append(f"已复制模板文件到 {new_variant_dir.relative_to(package_dir)}")

    texture_dir = _find_variant_texture_dir(new_variant_dir)
    if texture_dir is None:
        texture_dir = new_variant_dir / "TEXTURE.LIV"
        texture_dir.mkdir(parents=True, exist_ok=True)
        warnings.append("模板缺少 TEXTURE.* 目录，已创建 TEXTURE.LIV。")

    assets = builder.collect_assets(req.texture_dir)
    log.append("识别用户贴图：" + "、".join(a.name for a in assets))
    temp_dir = _make_temp_dir(package_dir.parent)
    try:
        builder._emit(progress, f"转换贴图（{len(assets)} 个，DDS）", 0.25)
        converted = builder.convert_assets(assets, temp_dir,
                                           targets=TargetFormat.DDS,
                                           quality_high=req.quality_high,
                                           progress=progress)
        _apply_texture_replacements(converted, texture_dir, warnings,
                                    log, suffix=".DDS")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # 更新 aircraft.cfg
    cfg_path = new_variant_dir / liverycfg.AIRCRAFT_CFG
    code = texture_dir.name.split(".", 1)[-1] if "." in texture_dir.name else "LIV"
    if cfg_path.is_file():
        old_text = cfg_path.read_text(encoding="utf-8-sig", errors="ignore")
        new_text = liverycfg.update_aircraft_cfg(old_text, req.info, texture_code=code)
        utils.atomic_write_bytes(cfg_path, new_text.encode("utf-8"))
        log.append("已更新 aircraft.cfg 的 [FLTSIM] 字段")
    else:
        liverycfg.write_aircraft_cfg(cfg_path, req.profile, req.info, code)
        warnings.append("模板缺少 aircraft.cfg，已按机型 profile 新建。")

    if req.profile.texture_cfg_fallbacks and not (texture_dir / "texture.CFG").exists():
        liverycfg.write_texture_cfg(texture_dir / "texture.CFG",
                                    req.profile.texture_cfg_fallbacks, generation="2020")

    builder._emit(progress, "重新生成缩略图", 0.8)
    sizes = thumbnails.thumbnail_sizes_for("2020", req.profile.thumbnail_sizes)
    written = thumbnails.generate_thumbnails(
        req.thumbnail_source, texture_dir, title=req.info.title,
        sizes=sizes, generation="2020", progress=progress)
    log.append("thumbnail：" + "、".join(p.name for p in written))

    layout_path, _manifest, manifest_path = builder._write_package_files(
        package_dir, req, log, progress)
    result = builder._finish(package_dir, new_variant_dir, manifest_path, layout_path,
                             converted, warnings, log, package_name=package_dir.name,
                             progress=progress)
    return result


def _apply_texture_replacements(converted, texture_dir: Path,
                                warnings: list[str], log: list[str],
                                *, suffix: str) -> tuple[list[str], list[str]]:
    """按基名匹配并替换模板纹理；返回 (替换列表, 保留列表)。"""
    template_files = _list_template_textures(texture_dir)
    by_base: dict[str, Path] = {}
    for path in template_files:
        by_base.setdefault(texture_basename(path.name), path)

    replaced: list[str] = []
    kept: list[str] = []
    unmatched: list[str] = []
    used_bases: set[str] = set()

    for item in converted:
        base = texture_basename(item.asset.name)
        target = by_base.get(base)
        if target is not None:
            _replace_with_converted(item, target, suffix=suffix)
            replaced.append(target.name)
            used_bases.add(base)
        else:
            new_path = texture_dir / f"{item.asset.name}{suffix}"
            new_path.parent.mkdir(parents=True, exist_ok=True)
            _replace_with_converted(item, new_path, suffix=suffix)
            unmatched.append(new_path.name)

    for path in template_files:
        base = texture_basename(path.name)
        if base in used_bases:
            continue
        kept.append(path.name)

    if replaced:
        warnings.append(f"已替换模板贴图 {len(replaced)} 个：" + "、".join(
            sorted(set(replaced))[:20]) + ("…" if len(set(replaced)) > 20 else ""))
    if kept:
        warnings.append(f"保留模板原有贴图 {len(kept)} 个（用户未提供同名贴图）：" + "、".join(
            sorted(set(kept))[:20]) + ("…" if len(set(kept)) > 20 else ""))
    if unmatched:
        warnings.append(f"用户贴图 {len(unmatched)} 个在模板中无同名项，已作为新增纹理写入：" + "、".join(
            sorted(set(unmatched))[:20]) + ("…" if len(set(unmatched)) > 20 else ""))
    log.append(f"贴图替换：替换 {len(replaced)}，保留 {len(kept)}，新增 {len(unmatched)}")
    return replaced, kept


__all__ = [
    "clone_from_template",
    "texture_basename",
]
