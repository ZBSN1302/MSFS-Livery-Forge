"""MSFS 2024 KTX2 容器读写（Asobo 自定义 DFD/KVD）。

真实 MSFS KTX2 是标准 Khronos KTX2 容器（supercompressionScheme=0）加一段
Asobo 自定义 KVD 元数据。本模块的写入器已用 ``docs/research/samples`` 里的
4 个真实文件（vkFormat 145/133/137/142）做过逐字节 round-trip 验证。

文件布局（全部 little-endian）::

    0    identifier        12B  AB 4B 54 58 20 32 30 BB 0D 0A 1A 0A
    12   vkFormat u32      142=BC5_SNORM, 133=BC1_RGBA_UNORM, 137=BC3_UNORM, 145=BC7_UNORM
    16   typeSize u32      恒 1
    20   pixelWidth/Height/Depth u32 ×3
    32   layerCount u32    = 0
    36   faceCount u32     = 1
    40   levelCount u32
    44   supercompressionScheme u32 = 0
    48   dfdByteOffset/Length u32 ×2
    56   kvdByteOffset/Length u32 ×2
    64   sgdByteOffset/Length u64 ×2 = 0
    80   level index：levelCount × (u64 offset, u64 length, u64 uncompressedByteLength)
         - dfdByteOffset 从 80+24*levelCount 开始
         - kvdByteOffset = dfdByteOffset + dfdByteLength
         - 数据区起点 = align16(kvdByteOffset + kvdByteLength)
         - mip 数据按 **level N-1（最小）→ level 0（最大）** 连续存放
         - level index 中的 offset 是文件内绝对偏移

KVD 是 TLV 序列，条目顺序固定为：``ASOBO_flags``、``ASOBO_opacities``、
``ASOBO_transp``（1 字节 0/1）、``ASOBOtexversion``（u32=1）、``KTXwriter``
（``ASOBO_FlightSim\\0``）。每条 = u32(key+value 长度) + key + NUL + value，
再补 0 到 4 字节对齐；**整体只补 0 到 4 字节对齐**（真实 COMP 高质量 KVD
为 212 字节，不能再补到 8 的倍数）。数据区起点仍按 16 字节对齐。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..types import TextureError, TextureKind
from ..utils import atomic_write_bytes
from .dds import read_dds_mips  # noqa: F401  （task 契约：从 ktx2 也能取 DDS mip）

KTX2_IDENTIFIER = bytes.fromhex("ab4b5458203230bb0d0a1a0a")
KTX2_HEADER_SIZE = 80
LEVEL_INDEX_ENTRY_SIZE = 24

#: 真实 MSFS 文件中出现的 vkFormat -> 每 4x4 块字节数
VK_BLOCK_BYTES: dict[int, int] = {145: 16, 142: 16, 137: 16, 133: 8, 139: 8, 131: 8}

#: 真实文件的 DFD 逐字节模板（44B 或 60B）。
DFD_HEX: dict[int, str] = {
    145: "2c00000000000000020028008601010003030000100000000000000000007f000000000000000000ffffffff",
    133: "2c00000000000000020028008001010003030000080000000000000000003f010000000000000000ffffffff",
    137: "3c00000000000000020038008201010003030000100000000000000000003f0f0000000000000000ffffffff"
         "40003f000000000000000000ffffffff",
    142: "3c00000000000000020038008401010003030000100000000000000000003f400000000000000080ffffff7f"
         "40003f410000000000000080ffffff7f",
}
DFD_TEMPLATES: dict[int, bytes] = {vk: bytes.fromhex(hex_str) for vk, hex_str in DFD_HEX.items()}

#: vkFormat -> texconv ``-f`` 名称（反向映射见 :data:`TEXCONV_TO_VK`）
VK_TO_TEXCONV: dict[int, str] = {
    145: "BC7_UNORM",
    133: "BC1_UNORM",
    137: "BC3_UNORM",
    142: "BC5_SNORM",
    139: "BC4_UNORM",
}
TEXCONV_TO_VK: dict[str, int] = {v: k for k, v in VK_TO_TEXCONV.items()}

VK_FORMAT_NAMES: dict[int, str] = {
    145: "BC7_UNORM",
    133: "BC1_RGBA_UNORM",
    137: "BC3_UNORM",
    142: "BC5_SNORM",
    139: "BC4_UNORM",
}

#: KVD ``ASOBO_flags`` 中的 token 顺序（来自真实文件，供 GUI 显示）
KVD_ENTRY_ORDER: tuple[str, ...] = (
    "ASOBO_flags",
    "ASOBO_opacities",
    "ASOBO_transp",
    "ASOBOtexversion",
    "KTXwriter",
)

#: (kind, quality_high) -> ASOBO_flags token 列表（顺序严格，来自 templates）
KVD_FLAGS: dict[tuple[TextureKind, bool], list[str]] = {
    (TextureKind.ALBEDO, True): ["BILINEAR", "COMPRESSION", "MIPMAP", "REDUCE_LESS",
                                 "PLATFORM_FORMAT", "QUALITY_HIGH"],
    (TextureKind.ALBEDO, False): ["BILINEAR", "COMPRESSION", "MIPMAP", "REDUCE_LESS",
                                  "PLATFORM_FORMAT"],
    (TextureKind.DECAL, True): ["BILINEAR", "COMPRESSION", "MIPMAP", "REDUCE_LESS",
                                "PLATFORM_FORMAT", "QUALITY_HIGH"],
    (TextureKind.DECAL, False): ["BILINEAR", "COMPRESSION", "MIPMAP", "REDUCE_LESS",
                                 "PLATFORM_FORMAT"],
    (TextureKind.COMP, True): ["BILINEAR", "COMPRESSION", "MIPMAP", "PLATFORM_FORMAT",
                               "QUALITY_HIGH", "NO_GAMMA_CORRECTION", "METAL_ROUGH_AO_DATA"],
    (TextureKind.COMP, False): ["BILINEAR", "COMPRESSION", "MIPMAP", "PLATFORM_FORMAT",
                                "NO_GAMMA_CORRECTION", "METAL_ROUGH_AO_DATA"],
    (TextureKind.NORMAL, True): ["BILINEAR", "COMPRESSION", "MIPMAP", "PLATFORM_FORMAT",
                                 "QUALITY_HIGH", "TANGENT_DXT5N", "NO_GAMMA_CORRECTION"],
    (TextureKind.NORMAL, False): ["BILINEAR", "COMPRESSION", "MIPMAP", "PLATFORM_FORMAT",
                                  "TANGENT_DXT5N", "NO_GAMMA_CORRECTION"],
    (TextureKind.EMISSIVE, True): ["BILINEAR", "COMPRESSION", "MIPMAP", "REDUCE_LESS",
                                   "PLATFORM_FORMAT", "QUALITY_HIGH"],
    (TextureKind.EMISSIVE, False): ["BILINEAR", "COMPRESSION", "MIPMAP", "REDUCE_LESS",
                                    "PLATFORM_FORMAT"],
    (TextureKind.GENERIC, True): ["BILINEAR", "COMPRESSION", "MIPMAP", "REDUCE_LESS",
                                  "PLATFORM_FORMAT", "QUALITY_HIGH"],
    (TextureKind.GENERIC, False): ["BILINEAR", "COMPRESSION", "MIPMAP", "REDUCE_LESS",
                                   "PLATFORM_FORMAT"],
}

#: (kind, quality_high) -> ``.KTX2.json`` 的 Flags 列表（顺序严格）
JSON_FLAGS: dict[tuple[TextureKind, bool], list[str]] = {
    (TextureKind.ALBEDO, True): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP",
                                 "FL_BITMAP_QUALITY_HIGH"],
    (TextureKind.ALBEDO, False): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP"],
    (TextureKind.DECAL, True): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP",
                                "FL_BITMAP_QUALITY_HIGH"],
    (TextureKind.DECAL, False): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP"],
    (TextureKind.COMP, True): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP",
                               "FL_BITMAP_QUALITY_HIGH", "FL_BITMAP_NO_GAMMA_CORRECTION",
                               "FL_BITMAP_METAL_ROUGH_AO_DATA"],
    (TextureKind.COMP, False): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP",
                                "FL_BITMAP_NO_GAMMA_CORRECTION",
                                "FL_BITMAP_METAL_ROUGH_AO_DATA"],
    (TextureKind.NORMAL, True): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP",
                                 "FL_BITMAP_QUALITY_HIGH", "FL_BITMAP_TANGENT_DXT5N",
                                 "FL_BITMAP_NO_GAMMA_CORRECTION"],
    (TextureKind.NORMAL, False): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP",
                                  "FL_BITMAP_TANGENT_DXT5N", "FL_BITMAP_NO_GAMMA_CORRECTION"],
    (TextureKind.EMISSIVE, True): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP",
                                   "FL_BITMAP_QUALITY_HIGH"],
    (TextureKind.EMISSIVE, False): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP"],
    (TextureKind.GENERIC, True): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP",
                                  "FL_BITMAP_QUALITY_HIGH"],
    (TextureKind.GENERIC, False): ["FL_BITMAP_COMPRESSION", "FL_BITMAP_MIPMAP"],
}

#: 固定条目值（来自真实文件）
KVD_OPACITIES_KEY = "ASOBO_opacities"
KVD_TEXVERSION = 1
KVD_WRITER = "ASOBO_FlightSim"


def _norm_kind(kind: TextureKind | str) -> TextureKind:
    try:
        return TextureKind(kind)
    except ValueError as exc:
        raise TextureError(f"未知的贴图类型：{kind!r}") from exc


def align_up(value: int, alignment: int) -> int:
    """向上对齐到 alignment 的整数倍。"""
    if alignment <= 0:
        raise ValueError("alignment 必须为正整数")
    return (value + alignment - 1) // alignment * alignment


def mip_chain_dims(width: int, height: int) -> list[tuple[int, int]]:
    """完整 mip 链尺寸列表（level0 → 1x1）。"""
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


def level_dims(width: int, height: int, level_count: int) -> list[tuple[int, int]]:
    """前 level_count 级 mip 的尺寸（允许不满整条链，例如只存 level0）。"""
    chain = mip_chain_dims(width, height)
    if not 1 <= level_count <= len(chain):
        raise TextureError(f"mip 级数无效：{level_count}（{width}x{height} 最多 {len(chain)} 级）")
    return chain[:level_count]


def mip_payload_size(width: int, height: int, vk_format: int) -> int:
    """按 vkFormat 计算某一级 mip 的字节数。"""
    bpb = VK_BLOCK_BYTES.get(vk_format)
    if bpb is None:
        raise TextureError(f"不支持的 vkFormat：{vk_format}")
    blocks = ((width + 3) // 4) * ((height + 3) // 4)
    return max(bpb, blocks * bpb)


def dfd_template(vk_format: int) -> bytes:
    """返回该 vkFormat 的 DFD 模板字节；不支持时抛 TextureError。"""
    dfd = DFD_TEMPLATES.get(vk_format)
    if dfd is None:
        raise TextureError(f"没有 vkFormat {vk_format} 的 DFD 模板")
    return dfd


def kvd_flags_for(kind: TextureKind | str, quality_high: bool) -> list[str]:
    """返回该贴图类型/质量档的 ASOBO_flags token 列表（副本）。"""
    key = (_norm_kind(kind), bool(quality_high))
    try:
        return list(KVD_FLAGS[key])
    except KeyError as exc:  # pragma: no cover - 所有 kind 都已定义
        raise TextureError(f"没有 {kind}/{quality_high} 的 KVD flags 模板") from exc


def json_flags_for(kind: TextureKind | str, quality_high: bool) -> list[str]:
    """返回该贴图类型/质量档的 ``.KTX2.json`` Flags 列表（副本）。"""
    key = (_norm_kind(kind), bool(quality_high))
    try:
        return list(JSON_FLAGS[key])
    except KeyError as exc:  # pragma: no cover
        raise TextureError(f"没有 {kind}/{quality_high} 的 JSON flags 模板") from exc


@dataclass
class KvdEntry:
    """一条 KVD 元数据。"""

    key: str
    value: bytes


def parse_kvd(kvd: bytes) -> list[KvdEntry]:
    """解析 KVD TLV 序列；遇到全 0 尾部填充时停止。"""
    entries: list[KvdEntry] = []
    pos = 0
    length = len(kvd)
    while pos + 4 <= length:
        (entry_len,) = struct.unpack_from("<I", kvd, pos)
        pos += 4
        if entry_len == 0:  # 尾部对齐填充
            break
        if pos + entry_len > length:
            raise TextureError(f"KVD 条目越界（entry_len={entry_len}，剩余 {length - pos} 字节）")
        blob = kvd[pos:pos + entry_len]
        pos += entry_len
        pos += (-(4 + entry_len)) % 4  # 条目补 0 到 4 字节对齐
        nul = blob.find(b"\x00")
        if nul < 0:
            key_bytes, value = blob, b""
        else:
            key_bytes, value = blob[:nul], blob[nul + 1:]
        entries.append(KvdEntry(key=key_bytes.decode("utf-8", "replace"), value=value))
    return entries


def build_kvd(flags: Sequence[str], has_transp: bool, opacities: bytes = b"") -> bytes:
    """按真实文件顺序构造 KVD 字节串。

    规则（对 78 个真实 MSFS 文件验证）：每条 entry 补 0 到 4 字节对齐；
    **整体不再额外补到 8 字节倍数**——真实 KVD 长度可能是 184/192/212 等
    任意 4 的倍数（例：COMP 高质量 212 字节）。多补 0 会让游戏读错元数据。
    """
    tokens = []
    for token in flags:
        if not token:
            continue
        if "\x00" in token:
            raise TextureError(f"KVD flag token 不能包含 NUL：{token!r}")
        tokens.append(token.encode("ascii") + b"\x00")
    entries: list[tuple[str, bytes]] = [
        ("ASOBO_flags", b"".join(tokens)),
        (KVD_OPACITIES_KEY, bytes(opacities)),
        ("ASOBO_transp", b"\x01" if has_transp else b"\x00"),
        ("ASOBOtexversion", struct.pack("<I", KVD_TEXVERSION)),
        ("KTXwriter", KVD_WRITER.encode("ascii") + b"\x00"),
    ]
    out = bytearray()
    for key, value in entries:
        blob = key.encode("ascii") + b"\x00" + value
        out += struct.pack("<I", len(blob))
        out += blob
        out += b"\x00" * ((-(4 + len(blob))) % 4)
    return bytes(out)


@dataclass
class Ktx2Level:
    """一级 mip 在文件中的位置与尺寸。"""

    level: int
    offset: int
    length: int
    uncompressed_length: int
    width: int
    height: int


@dataclass
class Ktx2File:
    """解析后的 MSFS KTX2。"""

    vk_format: int
    width: int
    height: int
    level_count: int
    dfd_offset: int
    dfd: bytes
    kvd_offset: int
    kvd: bytes
    levels: list[Ktx2Level] = field(default_factory=list)
    data: bytes = b""
    type_size: int = 1
    supercompression: int = 0
    path: Path | None = None

    @property
    def format_name(self) -> str:
        return VK_FORMAT_NAMES.get(self.vk_format, f"vkFormat={self.vk_format}")

    @property
    def mip_count(self) -> int:
        return self.level_count

    def read_level(self, level: int) -> bytes:
        """返回指定 level 的压缩 mip 数据。"""
        if not 0 <= level < len(self.levels):
            raise TextureError(f"mip level 越界：{level}（共 {len(self.levels)} 级）")
        info = self.levels[level]
        return self.data[info.offset:info.offset + info.length]

    def mip_payloads(self) -> list[bytes]:
        """按 level0 → 最小 mip 顺序返回全部 mip 数据。"""
        return [self.read_level(i) for i in range(self.level_count)]

    # 与 dds 模块命名保持一致，pipeline 可以互换使用
    payloads_level0_to_n = mip_payloads

    def kvd_entries(self) -> list[KvdEntry]:
        return parse_kvd(self.kvd)

    def flags(self) -> list[str]:
        """返回 ``ASOBO_flags`` 中的 token 列表。"""
        for entry in self.kvd_entries():
            if entry.key == "ASOBO_flags":
                return [t.decode("ascii", "replace")
                        for t in entry.value.split(b"\x00") if t]
        return []

    def has_transp(self) -> bool:
        for entry in self.kvd_entries():
            if entry.key == "ASOBO_transp":
                return entry.value[:1] == b"\x01"
        return False


def _read_source(source: str | Path | bytes) -> tuple[bytes, Path | None]:
    if isinstance(source, bytes):
        return source, None
    path = Path(source)
    try:
        return path.read_bytes(), path
    except OSError as exc:
        raise TextureError(f"无法读取 KTX2 文件：{path}（{exc}）") from exc


def parse_ktx2(source: str | Path | bytes) -> Ktx2File:
    """解析 MSFS KTX2 文件/字节流。"""
    data, path = _read_source(source)
    name = path.name if path is not None else "<内存数据>"
    if len(data) < KTX2_HEADER_SIZE:
        raise TextureError(f"KTX2 文件过短（{len(data)} 字节）：{name}")
    if data[:12] != KTX2_IDENTIFIER:
        raise TextureError(f"不是有效的 KTX2 文件（identifier 错误）：{name}")

    (vk_format, type_size, width, height, depth, layer_count, face_count,
     level_count, scheme) = struct.unpack_from("<9I", data, 12)
    dfd_offset, dfd_length, kvd_offset, kvd_length = struct.unpack_from("<4I", data, 48)

    if level_count == 0:
        raise TextureError(f"KTX2 levelCount 为 0：{name}")
    if width == 0 or height == 0:
        raise TextureError(f"KTX2 尺寸无效：{width}x{height}（{name}）")
    if vk_format not in VK_BLOCK_BYTES:
        raise TextureError(f"不支持的 KTX2 vkFormat {vk_format}（仅支持 MSFS BCn 纹理）：{name}")
    if scheme != 0:
        raise TextureError(f"不支持的 KTX2 超压缩方案 {scheme}（MSFS 使用 0）：{name}")
    index_end = KTX2_HEADER_SIZE + LEVEL_INDEX_ENTRY_SIZE * level_count
    if dfd_offset < index_end or dfd_offset + dfd_length > len(data):
        raise TextureError(f"KTX2 DFD 偏移/长度越界：{name}")
    if kvd_offset < dfd_offset + dfd_length or kvd_offset + kvd_length > len(data):
        raise TextureError(f"KTX2 KVD 偏移/长度越界：{name}")

    dims = level_dims(width, height, level_count)
    levels: list[Ktx2Level] = []
    for level in range(level_count):
        offset, length, uncompressed = struct.unpack_from(
            "<3Q", data, KTX2_HEADER_SIZE + level * LEVEL_INDEX_ENTRY_SIZE
        )
        if offset + length > len(data) or length == 0:
            raise TextureError(f"KTX2 level{level} 数据越界（offset={offset}, length={length}）：{name}")
        mw, mh = dims[level]
        expected = mip_payload_size(mw, mh, vk_format)
        if length != expected:
            raise TextureError(
                f"KTX2 level{level} 尺寸不符（{mw}x{mh} 应为 {expected} 字节，实际 {length}）：{name}"
            )
        levels.append(Ktx2Level(level=level, offset=offset, length=length,
                                uncompressed_length=uncompressed, width=mw, height=mh))

    return Ktx2File(
        vk_format=vk_format,
        width=width,
        height=height,
        level_count=level_count,
        dfd_offset=dfd_offset,
        dfd=data[dfd_offset:dfd_offset + dfd_length],
        kvd_offset=kvd_offset,
        kvd=data[kvd_offset:kvd_offset + kvd_length],
        levels=levels,
        data=data,
        type_size=type_size,
        supercompression=scheme,
        path=path,
    )


def build_ktx2_raw(vk_format: int, width: int, height: int,
                   mip_payloads_level0_to_n: Sequence[bytes],
                   dfd: bytes, kvd: bytes,
                   uncompressed_lengths: Sequence[int] | None = None) -> bytes:
    """用给定 DFD/KVD/mip 数据构造 KTX2 字节串（不重新生成元数据）。

    顺序：文件头 → level index → DFD → KVD → 0 填充到 16 字节对齐 →
    level N-1 ... level 0 的 mip 数据。
    """
    payloads = [bytes(p) for p in mip_payloads_level0_to_n]
    level_count = len(payloads)
    if level_count == 0:
        raise TextureError("至少需要一个 mip payload")
    dims = level_dims(width, height, level_count)
    expected = [mip_payload_size(w, h, vk_format) for w, h in dims]
    for level, (payload, size) in enumerate(zip(payloads, expected)):
        if len(payload) != size:
            raise TextureError(
                f"level{level} payload 长度 {len(payload)} 与预期 {size} 不符（{dims[level][0]}x{dims[level][1]}）"
            )
    if len(dfd) not in (44, 60):
        raise TextureError(f"DFD 长度异常：{len(dfd)}（应为 44 或 60）")
    if len(kvd) % 4 != 0:
        raise TextureError(f"KVD 长度必须是 4 的倍数：{len(kvd)}")

    header_len = KTX2_HEADER_SIZE + LEVEL_INDEX_ENTRY_SIZE * level_count
    dfd_offset = header_len
    kvd_offset = dfd_offset + len(dfd)
    data_start = align_up(kvd_offset + len(kvd), 16)

    if uncompressed_lengths is None:
        uncompressed = list(expected)
    else:
        if len(uncompressed_lengths) != level_count:
            raise TextureError("uncompressed_lengths 数量与 mip 级数不一致")
        uncompressed = [int(v) for v in uncompressed_lengths]

    # 数据按最小 mip -> level0 连续存放
    offsets = [0] * level_count
    blob = bytearray()
    cursor = data_start
    for level in range(level_count - 1, -1, -1):
        offsets[level] = cursor
        blob += payloads[level]
        cursor += len(payloads[level])

    out = bytearray()
    out += KTX2_IDENTIFIER
    out += struct.pack("<9I", int(vk_format), 1, width, height, 0, 0, 1, level_count, 0)
    out += struct.pack("<4I", dfd_offset, len(dfd), kvd_offset, len(kvd))
    out += struct.pack("<2Q", 0, 0)
    for level in range(level_count):
        out += struct.pack("<3Q", offsets[level], len(payloads[level]), uncompressed[level])
    if len(out) != header_len:  # pragma: no cover - 防御性断言
        raise TextureError(f"KTX2 头长度计算错误：{len(out)} != {header_len}")
    out += dfd
    out += kvd
    out += b"\x00" * (data_start - len(out))
    out += blob
    return bytes(out)


def build_ktx2(vk_format: int, width: int, height: int,
               mip_payloads_level0_to_n: Sequence[bytes],
               kind: TextureKind | str, quality_high: bool = True,
               has_transp: bool = False, opacities: bytes = b"",
               kvd_flags: Sequence[str] | None = None) -> bytes:
    """为转换后的 mip 链构造 MSFS KTX2（自动选择 DFD/KVD/Flags 模板）。

    参数
    ----
    vk_format: 145=BC7_UNORM、133=BC1_RGBA_UNORM、137=BC3_UNORM、142=BC5_SNORM。
    width,height: level0 像素尺寸（非 2 的幂请先交给 texconv ``-pow2``）。
    mip_payloads_level0_to_n: 从 level0（最大）到最小 mip 的压缩数据。
    kind: 贴图类型，决定 ASOBO_flags/KVD、JSON Flags。
    quality_high: 高质量档（写入 QUALITY_HIGH token）。
    has_transp: 源图是否含有效 alpha；写入 ``ASOBO_transp`` 条目。
    opacities: 可选 ``ASOBO_opacities`` 原始字节（真实高质 alpha 图会带 12 字节）。
    kvd_flags: 可选覆盖 token 列表（例如 ``mipmaps=False`` 时去掉 ``MIPMAP``）。
    """
    payloads = [bytes(p) for p in mip_payloads_level0_to_n]
    dfd = dfd_template(int(vk_format))
    flags = list(kvd_flags) if kvd_flags is not None else kvd_flags_for(kind, quality_high)
    kvd = build_kvd(flags, has_transp=has_transp, opacities=opacities)
    return build_ktx2_raw(int(vk_format), width, height, payloads, dfd, kvd)


def write_ktx2(path: str | Path, vk_format: int, width: int, height: int,
               mip_payloads_level0_to_n: Sequence[bytes],
               kind: TextureKind | str, quality_high: bool = True,
               has_transp: bool = False, opacities: bytes = b"",
               kvd_flags: Sequence[str] | None = None) -> Path:
    """生成并原子写入 KTX2 文件，返回输出路径。"""
    data = build_ktx2(vk_format, width, height, mip_payloads_level0_to_n,
                      kind, quality_high, has_transp, opacities, kvd_flags)
    out_path = Path(path)
    atomic_write_bytes(out_path, data)
    return out_path
