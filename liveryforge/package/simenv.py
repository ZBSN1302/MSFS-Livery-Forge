"""MSFS 环境探测与已安装涂装发现。

- :func:`detect_environment`：读取 ``%APPDATA%\\Microsoft Flight Simulator 2024\\
  UserCfg.opt`` 的 ``InstalledPackagesPath``，推导 Community 目录；探测失败不抛裸
  异常，``SimEnvironment.message`` 给出可读中文提示。
- :func:`discover_liveries`：遍历 Community，发现 2024 native（含 ``livery.cfg`` 的
  ``SimObjects/**/liveries/<creator>/<livery>/``）与 2020 legacy（``aircraft.cfg`` +
  ``TEXTURE.*``）涂装，供 GUI / 克隆模式使用。

支持环境变量覆盖（GUI 手动选择路径、测试用）：
``MSFS_COMMUNITY_DIR``、``MSFS_DATA_ROOT``、``MSFS_USER_CFG``。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from liveryforge.types import LiveryForgeError

INSTALLED_RE = re.compile(r'^\s*InstalledPackagesPath\s+"([^"]*)"', re.MULTILINE)


class DiscoveryError(LiveryForgeError):
    """Community 目录不可用或涂装发现失败（中文消息，可直接展示）。"""


@dataclass
class SimEnvironment:
    """一次探测结果（失败时 found=False，message 可展示）。"""

    found: bool = False
    generation: str = ""
    user_cfg: Path | None = None
    installed_packages: Path | None = None
    community: Path | None = None
    community_2024: Path | None = None
    community_dirs: list[Path] = field(default_factory=list)
    official: Path | None = None
    sim_exe: Path | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "generation": self.generation,
            "user_cfg": str(self.user_cfg) if self.user_cfg else "",
            "installed_packages": str(self.installed_packages) if self.installed_packages else "",
            "community": str(self.community) if self.community else "",
            "community_2024": str(self.community_2024) if self.community_2024 else "",
            "community_dirs": [str(path) for path in self.community_dirs],
            "official": str(self.official) if self.official else "",
            "sim_exe": str(self.sim_exe) if self.sim_exe else "",
            "message": self.message,
        }


@dataclass
class LiveryEntry:
    """一个已安装涂装（2024 native 或 2020 legacy）。"""

    package_dir: Path
    livery_dir: Path
    config_path: Path
    name: str = ""
    atc_id: str = ""
    atc_airline: str = ""
    icao_airline: str = ""
    texture_code: str = ""
    aircraft_path: str = ""
    creator: str = ""
    generation: str = "2024"
    thumbnail: Path | None = None
    texture_dir: Path | None = None

    def to_dict(self) -> dict:
        return {
            "package_dir": str(self.package_dir),
            "livery_dir": str(self.livery_dir),
            "config_path": str(self.config_path),
            "name": self.name,
            "atc_id": self.atc_id,
            "atc_airline": self.atc_airline,
            "icao_airline": self.icao_airline,
            "texture_code": self.texture_code,
            "aircraft_path": self.aircraft_path,
            "creator": self.creator,
            "generation": self.generation,
            "thumbnail": str(self.thumbnail) if self.thumbnail else "",
        }


# --------------------------------------------------------------------------
# UserCfg.opt 探测
# --------------------------------------------------------------------------
def user_cfg_candidates() -> list[Path]:
    """按优先级列出可能的 UserCfg.opt 路径。"""
    candidates: list[Path] = []
    override = os.environ.get("MSFS_USER_CFG")
    if override:
        candidates.append(Path(override))
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    localappdata = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local")
    candidates.extend([
        Path(appdata) / "Microsoft Flight Simulator 2024" / "UserCfg.opt",
        Path(appdata) / "Microsoft Flight Simulator" / "UserCfg.opt",
        Path(localappdata) / "Packages"
        / "Microsoft.FlightSimulator2024_8wekyb3d8bbwe" / "LocalCache" / "UserCfg.opt",
        Path(localappdata) / "Packages"
        / "Microsoft.FlightSimulator_8wekyb3d8bbwe" / "LocalCache" / "UserCfg.opt",
    ])
    return candidates


def read_installed_packages_path(user_cfg: Path) -> Path | None:
    """从 UserCfg.opt 读取 ``InstalledPackagesPath``（失败返回 None）。"""
    try:
        text = Path(user_cfg).read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return None
    match = INSTALLED_RE.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    return Path(value) if value else None


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.is_dir():
                return path
        except OSError:
            continue
    return None


def _detect_from_data_root(root: Path, *, user_cfg: Path | None,
                           generation: str) -> SimEnvironment:
    community = _first_existing([root / "Community"])
    community_2024 = _first_existing([root / "Community2024"])
    official = _first_existing([root / "Official2024", root / "Official2020"])
    community_dirs: list[Path] = []
    for path in (community, community_2024):
        if path is not None and path not in community_dirs:
            community_dirs.append(path)
    # 真实安装中 2024 涂装通常仍在 Community（Community2024 可能为空），
    # 因此优先使用 Community。
    effective = community or community_2024
    if effective is None:
        return SimEnvironment(
            found=False, generation=generation, user_cfg=user_cfg,
            installed_packages=root,
            message=f"已读取数据根 {root}，但其中没有 Community 目录。"
                    "请检查 MSFS 是否已启动过，或手动选择 Community 文件夹。")
    return SimEnvironment(
        found=True,
        generation=generation,
        user_cfg=user_cfg,
        installed_packages=root,
        community=effective,
        community_2024=community_2024,
        community_dirs=community_dirs,
        official=official,
        message=f"已探测到 MSFS {generation} 数据目录：{root}；Community：{effective}",
    )


def detect_environment() -> SimEnvironment:
    """自动探测 MSFS 环境；无论成功失败都返回 :class:`SimEnvironment`，不抛异常。"""
    try:
        override_community = os.environ.get("MSFS_COMMUNITY_DIR")
        if override_community:
            community = Path(override_community)
            if community.is_dir():
                return SimEnvironment(
                    found=True, generation="2024", community=community,
                    message=f"使用指定 Community 目录：{community}")
        override_root = os.environ.get("MSFS_DATA_ROOT")
        if override_root:
            root = Path(override_root)
            if root.is_dir():
                found = _detect_from_data_root(root, user_cfg=None, generation="2024")
                if found.found:
                    return found

        for user_cfg in user_cfg_candidates():
            if not user_cfg.is_file():
                continue
            installed = read_installed_packages_path(user_cfg)
            if installed is None:
                continue
            if not installed.is_dir():
                return SimEnvironment(
                    found=False, user_cfg=user_cfg, installed_packages=installed,
                    message=f"UserCfg.opt 指向的数据目录不存在：{installed}。"
                            "请确认 MSFS 安装/迁移后路径仍然有效，或手动选择 Community 目录。")
            generation = "2024" if "2024" in str(user_cfg) else "2020"
            detected = _detect_from_data_root(installed, user_cfg=user_cfg,
                                              generation=generation)
            if detected.found:
                return detected
            return detected
    except Exception as exc:  # 探测绝不能让程序崩溃
        return SimEnvironment(
            found=False,
            message=f"自动探测 MSFS 环境失败：{exc}。请手动选择 Community 目录。")

    return SimEnvironment(
        found=False,
        message="未找到 MSFS 的 UserCfg.opt，无法自动定位 Community 目录。"
                "请在设置中手动选择 Community 文件夹"
                "（通常形如 …\\Microsoft Flight Simulator\\Community）。")


# --------------------------------------------------------------------------
# cfg 解析（发现涂装用，轻量、容错）
# --------------------------------------------------------------------------
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KEY_RE = re.compile(r"^\s*([^=;#\s][^=]*?)\s*=\s*(.*?)\s*$")


def _strip_comment(value: str) -> str:
    in_quote = False
    for index, char in enumerate(value):
        if char == '"':
            in_quote = not in_quote
        elif char == ";" and not in_quote:
            return value[:index].rstrip()
    return value


def _parse_cfg(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    section = ""
    for raw_line in re.split(r"\r\n|\r|\n", text):
        line = raw_line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        match = _SECTION_RE.match(line)
        if match:
            section = match.group(1).strip().lower()
            result.setdefault(section, {})
            continue
        match = _KEY_RE.match(line)
        if match:
            key = match.group(1).strip().lower()
            value = _strip_comment(match.group(2).strip())
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            result.setdefault(section, {})[key] = value
    return result


def _read_cfg_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return ""


def _cfg_value(parsed: dict[str, dict[str, str]], key: str,
               sections: tuple[str, ...] = ("general", "fltsim")) -> str:
    for section in sections:
        value = parsed.get(section, {}).get(key)
        if value:
            return value
    for section_values in parsed.values():
        value = section_values.get(key)
        if value:
            return value
    return ""


def _find_child(directory: Path, name: str) -> Path | None:
    try:
        for child in directory.iterdir():
            if child.name.lower() == name.lower():
                return child
    except OSError:
        return None
    return None


def _find_thumbnail_2024(livery_dir: Path) -> Path | None:
    thumb_dir = _find_child(livery_dir, "thumbnail")
    if thumb_dir is None:
        return None
    preferred = ("thumbnail.png", "thumbnail.jpg", "thumbnail_small.png")
    for name in preferred:
        candidate = _find_child(thumb_dir, name)
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _find_texture_dir(variant_dir: Path) -> Path | None:
    try:
        for child in variant_dir.iterdir():
            if child.is_dir() and child.name.lower().startswith("texture."):
                return child
    except OSError:
        return None
    return None


def _iter_cfg_files(package_dir: Path, filename: str):
    """只在包的 SimObjects 子树里找 cfg（避免扫描整包/地景大目录）。"""
    simobjects = _find_child(package_dir, "SimObjects")
    if simobjects is None:
        return []
    try:
        return list(simobjects.rglob(filename))
    except OSError:
        return []


def _discover_2024(package_dir: Path) -> list[LiveryEntry]:
    entries: list[LiveryEntry] = []
    for cfg in _iter_cfg_files(package_dir, "livery.cfg"):
        livery_dir = cfg.parent
        liveries_dir = livery_dir.parent.parent
        if liveries_dir.name.lower() != "liveries":
            continue
        creator = livery_dir.parent.name
        aircraft_dir = liveries_dir.parent
        parsed = _parse_cfg(_read_cfg_text(cfg))
        title = _cfg_value(parsed, "name") or livery_dir.name
        entries.append(LiveryEntry(
            package_dir=package_dir,
            livery_dir=livery_dir,
            config_path=cfg,
            name=title,
            atc_id=_cfg_value(parsed, "atc_id"),
            atc_airline=_cfg_value(parsed, "atc_airline"),
            icao_airline=_cfg_value(parsed, "icao_airline"),
            aircraft_path=aircraft_dir.name,
            creator=creator,
            generation="2024",
            thumbnail=_find_thumbnail_2024(livery_dir),
            texture_dir=_find_child(livery_dir, "texture"),
        ))
    return entries


def _discover_2020(package_dir: Path) -> list[LiveryEntry]:
    entries: list[LiveryEntry] = []
    for cfg in _iter_cfg_files(package_dir, "aircraft.cfg"):
        variant_dir = cfg.parent
        texture_dir = _find_texture_dir(variant_dir)
        if texture_dir is None:
            continue
        parsed = _parse_cfg(_read_cfg_text(cfg))
        fltsim = next((name for name in parsed if name.startswith("fltsim")), "")
        section = parsed.get(fltsim, {}) if fltsim else {}
        code = section.get("texture", "") or texture_dir.name.split(".", 1)[-1]
        thumbnail = None
        for name in ("thumbnail.jpg", "thumbnail.png", "thumbnail_small.jpg"):
            candidate = _find_child(texture_dir, name)
            if candidate is not None and candidate.is_file():
                thumbnail = candidate
                break
        entries.append(LiveryEntry(
            package_dir=package_dir,
            livery_dir=variant_dir,
            config_path=cfg,
            name=section.get("title") or section.get("ui_variation") or variant_dir.name,
            atc_id=section.get("atc_id", ""),
            atc_airline=section.get("atc_airline", ""),
            icao_airline=section.get("icao_airline", ""),
            texture_code=code,
            aircraft_path=variant_dir.name,
            creator="",
            generation="2020",
            thumbnail=thumbnail,
            texture_dir=texture_dir,
        ))
    return entries


def discover_liveries(community_dir: Path, *, strict: bool = True) -> list[LiveryEntry]:
    """扫描 Community 目录下的所有涂装包。

    :param community_dir: Community 目录
    :param strict: 目录不存在时是否抛 :class:`DiscoveryError`
    :raises DiscoveryError: strict=True 且目录不存在
    """
    community_dir = Path(community_dir)
    if not community_dir.is_dir():
        message = (f"Community 目录不存在或不可访问：{community_dir}。"
                   "请检查 MSFS 数据目录设置，或在设置中手动选择 Community 文件夹。")
        if strict:
            raise DiscoveryError(message)
        return []

    entries: list[LiveryEntry] = []
    try:
        packages = sorted((p for p in community_dir.iterdir() if p.is_dir()),
                          key=lambda p: p.name.casefold())
    except OSError as exc:
        if strict:
            raise DiscoveryError(f"无法读取 Community 目录：{community_dir}（{exc}）") from exc
        return []

    for package_dir in packages:
        try:
            entries.extend(_discover_2024(package_dir))
            entries.extend(_discover_2020(package_dir))
        except OSError:
            continue
    entries.sort(key=lambda e: (e.package_dir.name.casefold(), e.name.casefold()))
    return entries


def discover_all_liveries(env_or_dir, *, strict: bool = False) -> list[LiveryEntry]:
    """扫描探测结果里的全部 Community 目录（Community + Community2024）。"""
    if isinstance(env_or_dir, SimEnvironment):
        dirs = list(env_or_dir.community_dirs)
        if not dirs and env_or_dir.community is not None:
            dirs = [env_or_dir.community]
    elif isinstance(env_or_dir, (str, Path)):
        dirs = [Path(env_or_dir)]
    else:
        dirs = [Path(path) for path in env_or_dir]

    entries: list[LiveryEntry] = []
    seen: set[str] = set()
    for directory in dirs:
        for entry in discover_liveries(directory, strict=strict):
            key = str(entry.config_path).casefold()
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    entries.sort(key=lambda e: (e.package_dir.name.casefold(), e.name.casefold()))
    return entries


def find_livery_dir(path: Path) -> tuple[Path, str]:
    """把用户选择的目录解析为 (livery 目录, 代际)。

    支持：直接选 livery 文件夹 / 选包根目录。找不到抛 :class:`DiscoveryError`。
    """
    path = Path(path)
    if not path.exists():
        raise DiscoveryError(f"路径不存在：{path}")
    if path.is_file():
        path = path.parent
    if (path / "livery.cfg").is_file():
        return path, "2024"
    if (path / "aircraft.cfg").is_file() and _find_texture_dir(path) is not None:
        return path, "2020"
    candidates_2024 = list(path.rglob("livery.cfg"))
    if len(candidates_2024) == 1:
        return candidates_2024[0].parent, "2024"
    candidates_2020 = list(path.rglob("aircraft.cfg"))
    if len(candidates_2020) == 1:
        return candidates_2020[0].parent, "2020"
    if len(candidates_2024) > 1 or len(candidates_2020) > 1:
        raise DiscoveryError(
            f"在 {path} 下发现多个涂装，请直接选择某一个涂装文件夹（含 livery.cfg 的目录）。")
    raise DiscoveryError(
        f"在 {path} 下没有找到 livery.cfg 或 aircraft.cfg，无法作为克隆模板。")


__all__ = [
    "DiscoveryError",
    "LiveryEntry",
    "SimEnvironment",
    "detect_environment",
    "discover_all_liveries",
    "discover_liveries",
    "find_livery_dir",
    "read_installed_packages_path",
    "user_cfg_candidates",
]
