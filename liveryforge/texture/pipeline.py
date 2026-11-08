"""纹理转换流水线：PNG/TIF → DDS + MSFS KTX2 + JSON sidecar。

对外唯一入口是 :func:`convert_textures`（契约见 ARCHITECTURE.md §5）::

    convert_textures(assets, out_dir, options, progress=None) -> list[ConvertedTexture]

流程（每个贴图）：

1. Pillow 读取源图尺寸/alpha；
2. 按 :class:`~liveryforge.types.TextureKind` + 质量档选出 BCn 格式；
3. 调用 texconv 生成带完整 mip 链的 DDS（``-f BCn -m 0 -dx10``）；
4. 解析 DDS mip 链，按真实 MSFS 规则封装 KTX2（DFD/KVD/level index）；
5. 输出 ``<原名>.KTX2`` + ``<原名>.KTX2.json``，以及 ``<原名>.DDS`` +
   ``<原名>.DDS.json``（targets 控制）。

约束：幂等（覆盖旧文件）、可并发（ThreadPoolExecutor）、失败抛带文件名的
:class:`~liveryforge.types.TextureError` 且不留下半成品文件（原子写入 + 清理）、
临时目录自动清理。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, UnidentifiedImageError

from ..types import (
    ConvertOptions,
    ConvertedTexture,
    TargetFormat,
    TextureAsset,
    TextureError,
    TextureKind,
)
from ..utils import atomic_write_bytes
from .classify import SUPPORTED_IMAGE_EXTS, classify, is_image_file, iter_image_files
from .dds import parse_dds
from .ktx2 import (
    VK_TO_TEXCONV,
    build_ktx2,
    json_flags_for,
    kvd_flags_for,
    level_dims,
    mip_payload_size,
)
from .texconv import convert_to_dds, find_texconv

ProgressFn = Callable[[str, "float | None"], None]

#: 1601-01-01 到 1970-01-01 的 100ns 数（Windows FILETIME 纪元差）
_WINDOWS_EPOCH_OFFSET = 116444736000000000


def filetime_for_path(path: str | Path) -> int:
    """源文件 mtime -> Windows FILETIME（100ns 精度，精确到文件系统记录值）。

    注意：共享 ``utils.filetime_for_path`` 用 float ``st_mtime``，在 FILETIME
    量级会丢精度；真实 MSFS sidecar 的 SourceFileDate 是精确值，故此处用
    ``st_mtime_ns // 100 + 116444736000000000``。
    """
    stat = Path(path).stat()
    return stat.st_mtime_ns // 100 + _WINDOWS_EPOCH_OFFSET

#: 含有效 alpha 时 JSON 写 ``"HasTransp": true`` 的贴图类型（真实包验证：
#: COMP/NORMAL 即使带 alpha 通道也不写 HasTransp）。
_ALPHA_TRANSP_KINDS = frozenset(
    {TextureKind.ALBEDO, TextureKind.DECAL, TextureKind.EMISSIVE, TextureKind.GENERIC}
)


@dataclass
class ImageProbe:
    """源图探测结果。"""

    width: int
    height: int
    alpha_present: bool  # 是否存在 alpha 通道/透明色
    has_alpha: bool      # alpha 通道中是否有非 255 的像素（有效的透明度）


def probe_image(source: str | Path) -> ImageProbe:
    """用 Pillow 读取源图尺寸与 alpha 信息。"""
    path = Path(source)
    try:
        with Image.open(path) as im:
            width, height = im.size
            bands = im.getbands()
            alpha_present = "A" in bands
            has_alpha = False
            if alpha_present:
                extrema = im.getchannel("A").getextrema()
                has_alpha = bool(extrema) and extrema[0] < 255
            elif "transparency" in im.info:
                rgba = im.convert("RGBA")
                extrema = rgba.getchannel("A").getextrema()
                has_alpha = bool(extrema) and extrema[0] < 255
                alpha_present = has_alpha
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise TextureError(f"无法读取图片：{path}（{exc}）") from exc
    return ImageProbe(width=width, height=height,
                      alpha_present=alpha_present, has_alpha=has_alpha)


def detect_has_alpha(source: str | Path) -> bool:
    """源图是否存在**有效** alpha（全 255 的 alpha 通道视为无透明）。"""
    return probe_image(source).has_alpha


def choose_format(kind: TextureKind | str, quality_high: bool,
                  has_alpha: bool) -> int:
    """按贴图类型/质量档/alpha 选择 vkFormat（=texconv 输出格式）。

    ====================== ========= ========== ==================
    类型                    高质量     标准       说明
    ====================== ========= ========== ==================
    ALBEDO / DECAL         BC7 (145) BC1 (133)  标准档有 alpha 时用 BC3 (137)
    COMP                   BC7 (145) BC1 (133)
    NORMAL                 BC5 (142) BC5 (142)  SNORM，无 alpha
    EMISSIVE / GENERIC     BC7 (145) BC7 (145)
    ====================== ========= ========== ==================
    """
    kind = TextureKind(kind)
    if kind is TextureKind.NORMAL:
        return 142
    if kind in (TextureKind.EMISSIVE, TextureKind.GENERIC):
        return 145
    if kind is TextureKind.COMP:
        return 145 if quality_high else 133
    # ALBEDO / DECAL
    if quality_high:
        return 145
    return 137 if has_alpha else 133


def build_sidecar_json(source_filetime: int, flags: Sequence[str],
                       has_transp: bool = False) -> bytes:
    """生成 MSFS sidecar JSON 字节串：紧凑单行、UTF-8 无 BOM、无结尾换行。

    字段顺序与真实文件一致：``Version``、``SourceFileDate``、``Flags``、可选 ``HasTransp``。
    """
    payload: dict[str, object] = {
        "Version": 2,
        "SourceFileDate": int(source_filetime),
        "Flags": [str(flag) for flag in flags],
    }
    if has_transp:
        payload["HasTransp"] = True
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _filter_flags(flags: Sequence[str], mipmaps: bool) -> list[str]:
    """没有 mipmap 时去掉 MIPMAP 相关标志。"""
    result = list(flags)
    if not mipmaps:
        result = [f for f in result if f not in ("MIPMAP", "FL_BITMAP_MIPMAP")]
    return result


def _asset_base_name(asset: TextureAsset) -> str:
    """输出文件基名：保留原始文件名（含扩展名），如 ``A320_ALBD.PNG``。"""
    name = Path(asset.name).name if asset.name else Path(asset.source).name
    if not name:
        raise TextureError(f"贴图缺少文件名：{asset.source}")
    return name


def _is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


def _create_temp_dir(parent: Path) -> Path:
    """在输出目录下创建临时工作目录。

    刻意不用 ``tempfile.mkdtemp``：它创建的目录在部分 Windows 沙箱/受控环境下
    会失去继承 ACL，导致随后无法写入（且 Python 会重试到卡死）；手动 ``mkdir``
    创建的目录可以正常继承权限。
    """
    for _ in range(100):
        candidate = parent / f".liveryforge_tmp_{os.getpid()}_{time.time_ns()}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
        except OSError as exc:
            raise TextureError(f"无法创建临时目录（{parent}）：{exc}") from exc
    raise TextureError(f"无法创建临时目录（{parent}）：重名过多")


def _strip_alpha_to_png(source: Path, dest: Path) -> Path:
    """把源图 alpha 丢弃后另存为临时 PNG（force_no_alpha 用）。"""
    try:
        with Image.open(source) as im:
            if "A" in im.getbands() or "transparency" in im.info:
                image = im.convert("RGBA").convert("RGB")
            else:
                image = im.convert("RGB")
            image.save(dest, format="PNG")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise TextureError(f"无法处理图片 alpha（{source}）：{exc}") from exc
    return dest


def _convert_one(asset: TextureAsset, out_dir: Path, options: ConvertOptions,
                 texconv_exe: Path) -> tuple[ConvertedTexture, list[str]]:
    """转换单个贴图（在工作线程中执行）。"""
    started = time.perf_counter()
    src = Path(asset.source)
    if not src.is_file():
        raise TextureError(f"找不到输入图片：{src}")
    if src.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
        raise TextureError(f"不支持的输入格式：{src.name}（支持 PNG/TIF 等常见图片）")

    kind = TextureKind(asset.kind)
    base = _asset_base_name(asset)
    probe = probe_image(src)
    has_alpha = probe.has_alpha and not options.force_no_alpha
    has_transp = has_alpha and kind in _ALPHA_TRANSP_KINDS

    warnings: list[str] = []
    pow2 = not (_is_power_of_two(probe.width) and _is_power_of_two(probe.height))
    if pow2:
        warnings.append(
            f"{base} 的尺寸 {probe.width}x{probe.height} 不是 2 的幂，"
            "已用 texconv -pow2 自动缩放（UV 可能轻微偏移）"
        )

    vk_format = choose_format(kind, options.quality_high, has_alpha)
    texconv_format = VK_TO_TEXCONV[vk_format]
    outputs: dict[str, Path] = {}
    tmp_dir = _create_temp_dir(out_dir)
    try:
        # force_no_alpha：转换前用 Pillow 丢弃 alpha（texconv 没有 -noalpha 选项）
        input_path = src
        if options.force_no_alpha and probe.alpha_present:
            stripped = _strip_alpha_to_png(src, tmp_dir / ("stripped" + src.suffix.lower()))
            input_path = stripped

        dds_tmp = convert_to_dds(
            input_path, texconv_format, tmp_dir,
            mipmaps=options.mipmaps,
            texconv=texconv_exe,
            timeout=options.timeout,
            pow2=pow2,
        )
        dds = parse_dds(dds_tmp)
        if dds.vk_format != vk_format:
            raise TextureError(
                f"texconv 输出格式不符（期望 vkFormat {vk_format}，"
                f"实际 {dds.vk_format} / {dds.format_name}）：{base}"
            )
        payloads = dds.mip_payloads()
        mip_dims = level_dims(dds.width, dds.height, dds.mip_count)
        for level, payload in enumerate(payloads):
            expected = mip_payload_size(mip_dims[level][0], mip_dims[level][1], vk_format)
            if len(payload) != expected:  # pragma: no cover - DDS 已校验过
                raise TextureError(
                    f"DDS mip{level} 数据长度异常（{len(payload)} != {expected}）：{base}"
                )

        source_filetime = filetime_for_path(src)

        if options.targets in (TargetFormat.KTX2, TargetFormat.BOTH):
            ktx_flags = _filter_flags(kvd_flags_for(kind, options.quality_high),
                                      options.mipmaps)
            ktx_bytes = build_ktx2(
                vk_format, dds.width, dds.height, payloads, kind,
                quality_high=options.quality_high,
                has_transp=has_transp,
                kvd_flags=ktx_flags,
            )
            ktx_path = out_dir / f"{base}.KTX2"
            atomic_write_bytes(ktx_path, ktx_bytes)
            outputs["ktx2"] = ktx_path

            json_flags = _filter_flags(json_flags_for(kind, options.quality_high),
                                       options.mipmaps)
            json_path = out_dir / f"{base}.KTX2.json"
            atomic_write_bytes(json_path, build_sidecar_json(
                source_filetime, json_flags, has_transp))
            outputs["ktx2_json"] = json_path

        if options.targets in (TargetFormat.DDS, TargetFormat.BOTH):
            dds_path = out_dir / f"{base}.DDS"
            atomic_write_bytes(dds_path, dds.data)
            outputs["dds"] = dds_path

            dds_flags = ["FL_BITMAP_COMPRESSION"]
            if options.mipmaps:
                dds_flags.append("FL_BITMAP_MIPMAP")
            dds_json_path = out_dir / f"{base}.DDS.json"
            atomic_write_bytes(dds_json_path, build_sidecar_json(
                source_filetime, dds_flags, has_transp))
            outputs["dds_json"] = dds_json_path

        elapsed = time.perf_counter() - started
        converted = ConvertedTexture(
            asset=asset,
            outputs=outputs,
            width=dds.width,
            height=dds.height,
            mip_levels=dds.mip_count,
            elapsed=elapsed,
        )
        return converted, warnings
    except BaseException:
        # 不留半成品：删除本次已经写出的文件
        for path in outputs.values():
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        if not options.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def convert_textures(assets: Iterable[TextureAsset], out_dir: str | Path,
                     options: ConvertOptions | None = None,
                     progress: ProgressFn | None = None) -> list[ConvertedTexture]:
    """把贴图批量转换为 DDS / KTX2（契约见 ARCHITECTURE.md §5）。

    :param assets: 待转换贴图列表（顺序即返回顺序）。
    :param out_dir: 输出目录（不存在时自动创建）。
    :param options: 转换选项；``None`` 时使用默认值（KTX2、高质量、完整 mip）。
    :param progress: 进度回调 ``fn(message: str, fraction: float | None)``。
    :returns: 与输入顺序一致的 :class:`ConvertedTexture` 列表。
    """
    options = options or ConvertOptions()
    asset_list = list(assets)
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    texconv_exe = find_texconv(options.texconv)

    # 输出文件名冲突提前报错，避免并发互相覆盖
    seen: dict[str, str] = {}
    for asset in asset_list:
        base = _asset_base_name(asset)
        key = base.casefold()
        if key in seen:
            raise TextureError(
                f"输出文件名冲突：{base}（同时来自 {seen[key]} 和 {asset.source}）"
            )
        seen[key] = str(asset.source)

    total = len(asset_list)
    if total == 0:
        return []

    workers = max(1, int(options.workers or 1))
    if progress is not None:
        progress(f"开始转换 {total} 个贴图（并发 {min(workers, total)}）...", 0.0)

    results: list[ConvertedTexture | None] = [None] * total
    with ThreadPoolExecutor(max_workers=min(workers, total), thread_name_prefix="liveryforge-tex") as pool:
        futures = {
            pool.submit(_convert_one, asset, destination, options, texconv_exe): index
            for index, asset in enumerate(asset_list)
        }
        done = 0
        try:
            for future in as_completed(futures):
                index = futures[future]
                try:
                    converted, warnings = future.result()
                except TextureError:
                    raise
                except Exception as exc:  # pragma: no cover - 防御性包装
                    raise TextureError(
                        f"转换失败：{asset_list[index].name or asset_list[index].source}（{exc}）"
                    ) from exc
                results[index] = converted
                done += 1
                if progress is not None:
                    for warning in warnings:
                        progress(f"警告：{warning}", None)
                    progress(
                        f"完成 {done}/{total}：{converted.asset.name} "
                        f"（{converted.width}x{converted.height}，"
                        f"{converted.mip_levels} 级 mip，{converted.elapsed:.2f}s）",
                        round(done / total, 6),
                    )
        except BaseException:
            for future in futures:
                future.cancel()
            raise

    if progress is not None:
        progress(f"全部完成：共 {total} 个贴图", 1.0)
    return [item for item in results if item is not None]


def discover_assets(directory: str | Path, *, recursive: bool = False) -> list[TextureAsset]:
    """扫描目录，把图片文件转成 :class:`TextureAsset`（供 CLI/GUI 使用）。

    跳过 ``.DDS`` 文件（避免把已转换产物当作输入重复处理）。
    """
    assets: list[TextureAsset] = []
    for path in iter_image_files(directory, recursive=recursive):
        if path.suffix.lower() == ".dds":
            continue
        if not is_image_file(path):
            continue
        assets.append(TextureAsset(source=path, name=path.name, kind=classify(path.name)))
    return assets
