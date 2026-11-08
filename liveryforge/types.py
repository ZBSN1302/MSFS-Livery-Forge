"""共享数据契约（冻结）：纹理引擎、涂装包引擎、GUI 都依赖本模块。

修改本文件前必须由 Lead 更新 ARCHITECTURE.md 并通知团队。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[str, "float | None"], None]


class TextureKind(str, Enum):
    ALBEDO = "albedo"
    COMP = "comp"
    NORMAL = "normal"
    DECAL = "decal"
    EMISSIVE = "emissive"
    GENERIC = "generic"

    @property
    def label_zh(self) -> str:
        return {
            TextureKind.ALBEDO: "基础色 (ALBD)",
            TextureKind.COMP: "材质 (COMP)",
            TextureKind.NORMAL: "法线 (NORM)",
            TextureKind.DECAL: "贴花 (DECAL)",
            TextureKind.EMISSIVE: "自发光 (EMIS)",
            TextureKind.GENERIC: "通用",
        }[self]


class TargetFormat(str, Enum):
    KTX2 = "ktx2"
    DDS = "dds"
    BOTH = "both"

    @property
    def label_zh(self) -> str:
        return {TargetFormat.KTX2: "KTX2 (MSFS 2024)", TargetFormat.DDS: "DDS (2020/legacy)",
                TargetFormat.BOTH: "KTX2 + DDS"}[self]


@dataclass
class TextureAsset:
    source: Path
    name: str
    kind: TextureKind = TextureKind.GENERIC

    @property
    def stem(self) -> str:
        """去掉最后一个扩展名的文件名，例如 A320_ALBD.PNG -> A320_ALBD。"""
        return Path(self.name).stem


@dataclass
class ConvertOptions:
    targets: TargetFormat = TargetFormat.KTX2
    quality_high: bool = True
    mipmaps: bool = True
    force_no_alpha: bool = False
    texconv: Path | None = None
    workers: int = 4
    timeout: int = 300
    keep_temp: bool = False


@dataclass
class ConvertedTexture:
    asset: TextureAsset
    outputs: dict[str, Path] = field(default_factory=dict)
    width: int = 0
    height: int = 0
    mip_levels: int = 0
    elapsed: float = 0.0


@dataclass
class LiveryInfo:
    title: str
    atc_id: str = ""
    atc_airline: str = ""
    icao_airline: str = ""
    ui_variation: str = ""
    author: str = ""
    version: str = "1.0"
    description: str = ""

    def normalized(self) -> "LiveryInfo":
        data = LiveryInfo(**self.__dict__)
        if not data.ui_variation:
            data.ui_variation = data.title
        return data


@dataclass
class Profile:
    id: str
    name: str
    generation: str  # "2024" | "2020"
    aircraft_path: str  # 相对包根，如 SimObjects/Airplanes/asobo_longitude
    creator_folder: str = "liveryforge"
    content_type: str = "LIVERY"  # LIVERY | AIRCRAFT
    package_order_hint: str = "SIMOBJECTS_PATCH"
    required_tags: str = ""
    texture_cfg_fallbacks: list[str] = field(default_factory=list)
    thumbnail_sizes: dict[str, int] = field(default_factory=dict)
    base_container: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "generation": self.generation,
            "aircraft_path": self.aircraft_path,
            "creator_folder": self.creator_folder,
            "content_type": self.content_type,
            "package_order_hint": self.package_order_hint,
            "required_tags": self.required_tags,
            "texture_cfg_fallbacks": list(self.texture_cfg_fallbacks),
            "thumbnail_sizes": dict(self.thumbnail_sizes),
            "base_container": self.base_container,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class BuildRequest:
    profile: Profile
    info: LiveryInfo
    texture_dir: Path
    output_root: Path
    formats: TargetFormat = TargetFormat.KTX2
    quality_high: bool = True
    thumbnail_source: Path | None = None
    include_json_sidecars: bool = True
    copy_to_community: bool = False
    community_dir: Path | None = None
    overwrite: bool = False
    author_slug: str = ""


@dataclass
class BuildResult:
    package_dir: Path
    livery_dir: Path
    manifest_path: Path
    layout_path: Path
    textures: list[ConvertedTexture] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    package_name: str = ""
    community_copied: bool = False


class LiveryForgeError(Exception):
    """所有面向用户的错误基类。"""


class TextureError(LiveryForgeError):
    pass


class PackageError(LiveryForgeError):
    pass


class ToolNotFoundError(LiveryForgeError):
    pass
