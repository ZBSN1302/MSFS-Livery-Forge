"""MSFS 纹理引擎。

模块划分：

- :mod:`~liveryforge.texture.classify`：文件名 → ``TextureKind``（中文标签辅助）
- :mod:`~liveryforge.texture.texconv`：内置 texconv 定位与调用
- :mod:`~liveryforge.texture.dds`：DDS（DX10/legacy）解析、mip 链尺寸
- :mod:`~liveryforge.texture.ktx2`：MSFS KTX2 容器解析/写入（DFD/KVD/level index）
- :mod:`~liveryforge.texture.pipeline`：PNG/TIF → DDS + KTX2 + JSON sidecar

主入口::

    from liveryforge.texture.pipeline import convert_textures
"""
from .classify import (
    SUPPORTED_IMAGE_EXTS,
    classify as classify_name,
    classify_path,
    describe,
    is_image_file,
    iter_image_files,
    kind_label,
)
from .dds import DdsFile, DdsMip, dds_mip_size, parse_dds, read_dds_mips
from .ktx2 import (
    DFD_TEMPLATES,
    KTX2_IDENTIFIER,
    Ktx2File,
    Ktx2Level,
    KvdEntry,
    build_ktx2,
    build_ktx2_raw,
    build_kvd,
    json_flags_for,
    kvd_flags_for,
    parse_ktx2,
    parse_kvd,
    write_ktx2,
)
from .pipeline import (
    build_sidecar_json,
    choose_format,
    convert_textures,
    detect_has_alpha,
    discover_assets,
    filetime_for_path,
    probe_image,
)
from .texconv import convert_to_dds, find_texconv, resolve_format

__all__ = [
    # classify
    "SUPPORTED_IMAGE_EXTS", "classify_name", "classify_path", "describe", "is_image_file",
    "iter_image_files", "kind_label",
    # dds
    "DdsFile", "DdsMip", "dds_mip_size", "parse_dds", "read_dds_mips",
    # ktx2
    "DFD_TEMPLATES", "KTX2_IDENTIFIER", "Ktx2File", "Ktx2Level", "KvdEntry",
    "build_ktx2", "build_ktx2_raw", "build_kvd", "json_flags_for", "kvd_flags_for",
    "parse_ktx2", "parse_kvd", "write_ktx2",
    # pipeline
    "build_sidecar_json", "choose_format", "convert_textures", "detect_has_alpha",
    "discover_assets", "filetime_for_path", "probe_image",
    # texconv
    "convert_to_dds", "find_texconv", "resolve_format",
]
