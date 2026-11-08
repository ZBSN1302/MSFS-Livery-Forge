"""DDS 解析：同时支持 DX10 扩展头与 legacy（DXT1/DXT5/BC5S 等 fourCC）头。

MSFS 纹理只使用 BCn 压缩格式（BC7/BC1/BC3/BC5/BC4），因此本模块只解析
这些格式的 mip 链；遇到未压缩或未知格式会抛出带中文信息的
:class:`~liveryforge.types.TextureError`。

关键偏移（little-endian）::

    0    magic "DDS "
    4    DDS_HEADER（dwSize=124）:
           4  dwSize      8  dwFlags   12 dwHeight  16 dwWidth
           20 dwPitch     24 dwDepth   28 dwMipMapCount
           76 DDS_PIXELFORMAT (dwSize=32): 80 dwFlags, 84 dwFourCC
           108 dwCaps    112 dwCaps2  116 dwCaps3  120 dwCaps4  124 dwReserved2
    128  [DX10] dxgiFormat(128) resourceDimension(132) miscFlag(136)
                arraySize(140) miscFlags2(144)   -> 数据从 148 开始
    128  [legacy] 数据即从 128 开始
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..types import TextureError

DDS_MAGIC = b"DDS "
DDS_HEADER_SIZE = 124
DDS_MIN_SIZE = 128  # magic + DDS_HEADER
DDS_DX10_HEADER_SIZE = 20

# DDS_PIXELFORMAT.dwFlags
DDPF_FOURCC = 0x00000004
DDPF_RGB = 0x00000040

# DDS_HEADER.dwCaps
DDSCAPS_TEXTURE = 0x00001000
DDSCAPS_MIPMAP = 0x00400000
DDSCAPS_COMPLEX = 0x00000008

D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3

#: legacy fourCC -> (每 4x4 块字节数, MSFS 使用的 vkFormat)
_FOURCC_INFO: dict[bytes, tuple[int, int]] = {
    b"DXT1": (8, 133),   # BC1 -> BC1_RGBA_UNORM_BLOCK
    b"DXT5": (16, 137),  # BC3 -> BC3_UNORM_BLOCK
    b"BC4U": (8, 139),   # BC4_UNORM
    b"BC4S": (8, 140),   # BC4_SNORM
    b"BC5U": (16, 141),  # BC5_UNORM
    b"BC5S": (16, 142),  # BC5_SNORM（MSFS 法线贴图使用）
    b"ATI1": (8, 139),   # 旧 ATI BC4_UNORM
    b"ATI2": (16, 141),  # 旧 ATI BC5_UNORM
}

#: DXGI_FORMAT -> (每 4x4 块字节数, MSFS vkFormat, 名称)
DXGI_INFO: dict[int, tuple[int, int, str]] = {
    71: (8, 133, "BC1_UNORM"),
    72: (8, 133, "BC1_UNORM_SRGB"),
    77: (16, 137, "BC3_UNORM"),
    78: (16, 137, "BC3_UNORM_SRGB"),
    80: (8, 139, "BC4_UNORM"),
    81: (8, 140, "BC4_SNORM"),
    83: (16, 141, "BC5_UNORM"),
    84: (16, 142, "BC5_SNORM"),
    98: (16, 145, "BC7_UNORM"),
    99: (16, 145, "BC7_UNORM_SRGB"),
}

#: texconv 的 ``-f`` 格式名 -> (块字节数, vkFormat)
TEXCONV_FORMAT_INFO: dict[str, tuple[int, int]] = {
    "BC1_UNORM": (8, 133),
    "BC3_UNORM": (16, 137),
    "BC4_UNORM": (8, 139),
    "BC5_SNORM": (16, 142),
    "BC7_UNORM": (16, 145),
}

_FORMAT_NAMES = {
    145: "BC7_UNORM",
    133: "BC1_UNORM",
    137: "BC3_UNORM",
    142: "BC5_SNORM",
    141: "BC5_UNORM",
    139: "BC4_UNORM",
    140: "BC4_SNORM",
}


def block_bytes(format_id: int) -> int:
    """按 vkFormat 或 DXGI_FORMAT 返回每 4x4 块字节数；未知格式抛 TextureError。"""
    if format_id in {145, 137, 142, 139, 141, 140, 133, 131, 132}:
        return 8 if format_id in (139, 140, 131, 132, 133) else 16
    if format_id in DXGI_INFO:
        return DXGI_INFO[format_id][0]
    raise TextureError(f"不支持的纹理格式编号：{format_id}")


def mip_chain_dims(width: int, height: int) -> list[tuple[int, int]]:
    """返回完整 mip 链尺寸（level0 → 1x1）。"""
    if width <= 0 or height <= 0:
        raise TextureError(f"无效的纹理尺寸：{width}x{height}")
    dims: list[tuple[int, int]] = []
    w, h = width, height
    while True:
        dims.append((w, h))
        if w == 1 and h == 1:
            break
        w, h = max(1, w // 2), max(1, h // 2)
    return dims


def dds_mip_size(width: int, height: int, bytes_per_block: int) -> int:
    """BCn mip 的存储字节数：块数 × 块字节数，最小一个块。"""
    blocks = ((width + 3) // 4) * ((height + 3) // 4)
    return max(bytes_per_block, blocks * bytes_per_block)


@dataclass
class DdsMip:
    """一级 mip 的元数据（数据本体在 :attr:`DdsFile.data` 中）。"""

    level: int
    width: int
    height: int
    offset: int
    size: int


@dataclass
class DdsFile:
    """解析后的 DDS（仅 BCn 2D 纹理）。"""

    width: int
    height: int
    depth: int
    mip_count: int
    flags: int
    pitch: int
    caps: int
    caps2: int
    dxgi_format: int | None
    fourcc: bytes | None
    array_size: int
    resource_dimension: int
    misc_flag: int
    misc_flags2: int
    header_size: int
    data_offset: int
    bytes_per_block: int
    vk_format: int | None
    mips: list[DdsMip] = field(default_factory=list)
    data: bytes = b""
    path: Path | None = None

    @property
    def is_dx10(self) -> bool:
        return self.dxgi_format is not None

    @property
    def format_name(self) -> str:
        if self.vk_format is not None and self.vk_format in _FORMAT_NAMES:
            return _FORMAT_NAMES[self.vk_format]
        if self.fourcc is not None:
            return self.fourcc.decode("ascii", "replace")
        if self.dxgi_format is not None:
            return f"DXGI_{self.dxgi_format}"
        return "未知"

    def mip_data(self, level: int) -> bytes:
        """返回指定 level 的原始压缩数据。"""
        if not 0 <= level < len(self.mips):
            raise TextureError(f"mip level 越界：{level}（共 {len(self.mips)} 级）")
        mip = self.mips[level]
        return self.data[mip.offset:mip.offset + mip.size]

    def mip_payloads(self) -> list[bytes]:
        """按 level0 → 最小 mip 的顺序返回全部 mip 数据（KTX2 写入顺序）。"""
        return [self.mip_data(i) for i in range(len(self.mips))]


def _read_source(source: str | Path | bytes) -> tuple[bytes, Path | None]:
    if isinstance(source, bytes):
        return source, None
    path = Path(source)
    try:
        return path.read_bytes(), path
    except OSError as exc:
        raise TextureError(f"无法读取 DDS 文件：{path}（{exc}）") from exc


def parse_dds(source: str | Path | bytes) -> DdsFile:
    """解析 DDS 文件/字节流，返回 :class:`DdsFile`。"""
    data, path = _read_source(source)
    name = path.name if path is not None else "<内存数据>"
    if len(data) < DDS_MIN_SIZE:
        raise TextureError(f"DDS 文件过短（{len(data)} 字节）：{name}")
    if data[:4] != DDS_MAGIC:
        raise TextureError(f"不是有效的 DDS 文件（magic 错误）：{name}")

    (header_size,) = struct.unpack_from("<I", data, 4)
    if header_size != DDS_HEADER_SIZE:
        raise TextureError(f"DDS 头大小异常（{header_size}，应为 {DDS_HEADER_SIZE}）：{name}")

    (size, flags, height, width, pitch, depth, mip_count) = struct.unpack_from("<7I", data, 4)
    caps, caps2 = struct.unpack_from("<2I", data, 108)

    pf_size, pf_flags = struct.unpack_from("<2I", data, 76)
    if pf_size != 32:
        raise TextureError(f"DDS 像素格式头大小异常（{pf_size}）：{name}")
    fourcc = data[84:88]
    dxgi_format: int | None = None
    array_size = 1
    resource_dimension = D3D10_RESOURCE_DIMENSION_TEXTURE2D
    misc_flag = 0
    misc_flags2 = 0
    data_offset = DDS_MIN_SIZE

    if fourcc == b"DX10":
        if len(data) < DDS_MIN_SIZE + DDS_DX10_HEADER_SIZE:
            raise TextureError(f"DDS DX10 扩展头缺失：{name}")
        (dxgi_format, resource_dimension, misc_flag, array_size, misc_flags2) = struct.unpack_from(
            "<5I", data, 128
        )
        data_offset = DDS_MIN_SIZE + DDS_DX10_HEADER_SIZE
        info = DXGI_INFO.get(dxgi_format)
        if info is None:
            raise TextureError(
                f"不支持的 DXGI 格式 {dxgi_format}（只支持 BC1/BC3/BC4/BC5/BC7）：{name}"
            )
        block, vk_format, _fmt_name = info
    else:
        if not (pf_flags & DDPF_FOURCC):
            raise TextureError(f"不支持的 DDS 像素格式（非 fourCC）：{name}")
        fourcc_info = _FOURCC_INFO.get(fourcc)
        if fourcc_info is None:
            shown = fourcc.decode("ascii", "replace")
            raise TextureError(f"不支持的 DDS fourCC 『{shown}』：{name}")
        block, vk_format = fourcc_info

    if width <= 0 or height <= 0:
        raise TextureError(f"DDS 尺寸无效：{width}x{height}（{name}）")
    if array_size not in (0, 1):
        raise TextureError(f"不支持的 DDS 数组纹理（arraySize={array_size}）：{name}")
    if depth not in (0, 1):
        raise TextureError(f"不支持的 DDS 3D 纹理（depth={depth}）：{name}")
    if dxgi_format is not None and resource_dimension not in (
        D3D10_RESOURCE_DIMENSION_TEXTURE2D, 0
    ):
        raise TextureError(
            f"不支持的 DDS 资源维度（dimension={resource_dimension}，仅支持 2D）：{name}"
        )

    chain = mip_chain_dims(width, height)
    if mip_count == 0:
        mip_count = 1
    if mip_count > len(chain):
        raise TextureError(
            f"DDS mip 数异常：{mip_count}（{width}x{height} 最多 {len(chain)} 级）：{name}"
        )

    mips: list[DdsMip] = []
    offset = data_offset
    for level in range(mip_count):
        mw, mh = chain[level]
        size = dds_mip_size(mw, mh, block)
        if offset + size > len(data):
            raise TextureError(
                f"DDS 数据区不完整（level{level} 需要 {size} 字节，剩余 {len(data) - offset}）：{name}"
            )
        mips.append(DdsMip(level=level, width=mw, height=mh, offset=offset, size=size))
        offset += size

    return DdsFile(
        width=width,
        height=height,
        depth=depth or 1,
        mip_count=mip_count,
        flags=flags,
        pitch=pitch,
        caps=caps,
        caps2=caps2,
        dxgi_format=dxgi_format,
        fourcc=None if dxgi_format is not None else fourcc,
        array_size=max(1, array_size),
        resource_dimension=resource_dimension,
        misc_flag=misc_flag,
        misc_flags2=misc_flags2,
        header_size=data_offset,
        data_offset=data_offset,
        bytes_per_block=block,
        vk_format=vk_format,
        mips=mips,
        data=data,
        path=path,
    )


def read_dds_mips(source: str | Path | bytes) -> list[bytes]:
    """读取 DDS 的 mip 链，返回 level0 → 最小 mip 的字节列表。"""
    return parse_dds(source).mip_payloads()
