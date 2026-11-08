"""livery.cfg / texture.cfg / legacy aircraft.cfg 生成与原地更新。

真实样本（E:\\历史\\Community）：
- 2024 livery.cfg：CRLF、UTF-8 无 BOM、末尾无换行。
  * Longitude: ``[Version] major/minor`` + ``[GENERAL] Name="..."``
  * A380: ``[version]`` + ``[GENERAL] Name`` + ``[FLTSIM] atc_id/atc_airline/icao_airline``
  * PMDG: ``[Version]`` + ``[Selection] required_tags`` + ``[GENERAL]
    name/atc_id/atc_airline/icao_airline/ui_variation``
  * A350: ``[General]`` + ``[Selection] required_tags="ext_a359"`` +
    ``[Panel_DynamicParameters] param.0=...``
- 2024 texture.cfg：``texture/`` 下，``[fltsim]`` 紧接 fallback.N=...（真实 Longitude）。
- 2020 legacy aircraft.cfg：``[VERSION] major/minor``、``[VARIATION]
  base_container``、``;===== FLTSIM =====`` 注释、``[FLTSIM.0]`` 各字段（真实 FBW A320）。
- 2020 TEXTURE.<X>/texture.CFG：``[fltsim]`` 后空一行再 fallback.N（真实 FBW）。

模板策略：机型差异（节名/键名大小写）由 profile JSON 里的
``livery_cfg_template`` / ``aircraft_cfg_template`` 模板字符串描述，字段用
``{title}``、``{atc_id}`` 等占位符；整行占位符都为空时该行自动省略，
空节自动移除。模板缺失时回退到本模块内置通用模板。
"""
from __future__ import annotations

import re
from pathlib import Path

from liveryforge import utils
from liveryforge.types import LiveryInfo, Profile

LIVERY_CFG = "livery.cfg"
AIRCRAFT_CFG = "aircraft.cfg"
TEXTURE_CFG = "texture.cfg"

FLTSIM_COMMENT = ";===================== FLTSIM ====================="

#: 通用 2024 livery.cfg 模板（各机型可在 profile JSON 覆盖）
DEFAULT_LIVERY_CFG_TEMPLATE = '''[Version]
major = 1
minor = 0

[GENERAL]
name = "{title}"
atc_id = "{atc_id}"
atc_airline = "{atc_airline}"
icao_airline = "{icao_airline}"
ui_variation = "{ui_variation}"

[Selection]
required_tags = "{required_tags}"'''

#: 通用 2020 aircraft.cfg 模板
DEFAULT_AIRCRAFT_CFG_TEMPLATE = '''[VERSION]
major = 1
minor = 0

[VARIATION]
base_container = "{base_container}"

;===================== FLTSIM =====================

[FLTSIM.0]
title = "{title}" ; Variation name
texture = "{texture}" ; texture folder
ui_variation = "{ui_variation}" ; e.g. World Air, IFR Panel
atc_id = "{atc_id}" ; tail number
atc_id_enable = 0 ; enable tail number
atc_airline = "{atc_airline}" ; airline name
atc_flight_number = "{atc_flight_number}" ; flight number
icao_airline = "{icao_airline}"'''

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KEY_RE = re.compile(r"^\s*([^=;#\s][^=]*?)\s*=\s*(.*?)\s*$")


# --------------------------------------------------------------------------
# 模板渲染
# --------------------------------------------------------------------------
def cfg_values(profile: Profile, info: LiveryInfo, *, texture_code: str = "") -> dict[str, str]:
    """模板占位符取值。"""
    data = info.normalized()
    return {
        "title": data.title,
        "name": data.title,
        "ui_variation": data.ui_variation,
        "atc_id": data.atc_id,
        "atc_airline": data.atc_airline,
        "icao_airline": data.icao_airline,
        "atc_flight_number": "",
        "atc_id_enable": "0",
        "required_tags": profile.required_tags or "",
        "base_container": profile.base_container or "",
        "texture": texture_code,
        "author": data.author,
        "version": data.version,
        "description": data.description,
        "creator_folder": profile.creator_folder,
        "aircraft_path": profile.aircraft_path,
        "panel_param_0": "",
    }


def render_cfg_template(template: str, values: dict[str, str]) -> str:
    """渲染 cfg 模板：整行字段全为空则省略该行，空节移除，CRLF、无尾换行。"""
    kept: list[str] = []
    for raw_line in re.split(r"\r\n|\r|\n", str(template)):
        names = _PLACEHOLDER_RE.findall(raw_line)
        if names and all(not str(values.get(name, "") or "").strip() for name in names):
            continue
        kept.append(_PLACEHOLDER_RE.sub(
            lambda m: str(values.get(m.group(1), "") or ""), raw_line))

    # 以空行分块，删除只剩节头的空块（例如 [Selection] 的键被省略）
    blocks: list[list[str]] = [[]]
    for line in kept:
        if line.strip():
            blocks[-1].append(line)
        else:
            blocks.append([])
    cleaned = [block for block in blocks
               if block and not (len(block) == 1 and _SECTION_RE.match(block[0].strip()))]
    return "\r\n\r\n".join("\r\n".join(block) for block in cleaned)


def _template_for(profile: Profile, attr: str, default: str) -> str:
    template = getattr(profile, attr, "") or ""
    if not template:
        extra = getattr(profile, "_extra", None) or {}
        template = extra.get(attr, "") or ""
    return template or default


# --------------------------------------------------------------------------
# 2024 livery.cfg
# --------------------------------------------------------------------------
def build_livery_cfg(profile: Profile, info: LiveryInfo) -> str:
    """生成 2024 native 涂装 ``livery.cfg`` 文本（CRLF，无 BOM，无尾换行）。"""
    template = _template_for(profile, "livery_cfg_template", DEFAULT_LIVERY_CFG_TEMPLATE)
    text = render_cfg_template(template, cfg_values(profile, info))
    return text.rstrip("\r\n")


def write_livery_cfg(path: Path, profile: Profile, info: LiveryInfo) -> Path:
    path = Path(path)
    utils.atomic_write_bytes(path, build_livery_cfg(profile, info).encode("utf-8"))
    return path


# --------------------------------------------------------------------------
# texture.cfg（2024 与 2020 通用）
# --------------------------------------------------------------------------
def build_texture_cfg(fallbacks: list[str], *, generation: str = "2024") -> str:
    """由 profile.texture_cfg_fallbacks 生成 texture.cfg 文本。

    值按真实样本原样写出（相对 texture/ 目录的 Windows 反斜杠路径）。
    2020 legacy 真实样本在 ``[fltsim]`` 后空一行，2024 样本不空。
    """
    lines = ["[fltsim]"]
    if str(generation) == "2020":
        lines.append("")
    for index, fallback in enumerate(fallbacks, start=1):
        lines.append(f"fallback.{index}={fallback}")
    return "\r\n".join(lines)


def write_texture_cfg(path: Path, fallbacks: list[str], *, generation: str = "2024") -> Path:
    path = Path(path)
    utils.atomic_write_bytes(path, build_texture_cfg(fallbacks, generation=generation)
                             .encode("utf-8"))
    return path


# --------------------------------------------------------------------------
# 2020 legacy aircraft.cfg
# --------------------------------------------------------------------------
def build_aircraft_cfg(profile: Profile, info: LiveryInfo, texture_code: str) -> str:
    """生成 2020 legacy ``aircraft.cfg`` 文本（CRLF，无 BOM，无尾换行）。"""
    template = _template_for(profile, "aircraft_cfg_template", DEFAULT_AIRCRAFT_CFG_TEMPLATE)
    values = cfg_values(profile, info, texture_code=texture_code)
    if not values.get("base_container") and profile.base_container:
        values["base_container"] = profile.base_container
    text = render_cfg_template(template, values)
    if profile.required_tags:
        text = f'{text}\r\n\r\n[Selection]\r\nrequired_tags = "{profile.required_tags}"'
    return text.rstrip("\r\n")


def write_aircraft_cfg(path: Path, profile: Profile, info: LiveryInfo,
                       texture_code: str) -> Path:
    path = Path(path)
    utils.atomic_write_bytes(path, build_aircraft_cfg(profile, info, texture_code)
                             .encode("utf-8"))
    return path


# --------------------------------------------------------------------------
# 解析与更新（克隆模式）
# --------------------------------------------------------------------------
def _strip_comment(value: str) -> str:
    """去掉行尾注释；引号内的 ';' 不视为注释。"""
    in_quote = False
    for index, char in enumerate(value):
        if char == '"':
            in_quote = not in_quote
        elif char == ";" and not in_quote:
            return value[:index].rstrip()
    return value


def parse_cfg(text: str) -> dict[str, dict[str, str]]:
    """解析 cfg 为 {节名小写: {键小写: 去引号值}}（保留首次出现，注释忽略）。"""
    result: dict[str, dict[str, str]] = {}
    section = ""
    for raw_line in re.split(r"\r\n|\r|\n", str(text)):
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


def _split_lines(text: str) -> list[str]:
    return re.split(r"\r\n|\r|\n", str(text))


def _section_span(lines: list[str], name: str) -> tuple[int, int] | None:
    """返回节头行号与节结束行号（不含）。name 不区分大小写。"""
    target = name.strip().lower()
    start = None
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        match = _SECTION_RE.match(stripped)
        if match:
            if start is not None:
                return start, index
            if match.group(1).strip().lower() == target:
                start = index
    if start is not None:
        return start, len(lines)
    return None


def _find_key(lines: list[str], start: int, end: int, key: str) -> int | None:
    target = key.lower()
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith(";"):
            continue
        match = _KEY_RE.match(stripped)
        if match and match.group(1).strip().lower() == target:
            return index
    return None


def _set_value(lines: list[str], section: str, key: str, value: str) -> bool:
    """在指定节中设置 key=value；原地更新保留原键名，缺失则追加到节尾。

    返回 True 表示该节存在并已写入。
    """
    span = _section_span(lines, section)
    if span is None:
        return False
    start, end = span
    index = _find_key(lines, start, end, key)
    if index is not None:
        original = _KEY_RE.match(lines[index].strip())
        original_key = original.group(1).strip() if original else key
        lines[index] = f'{original_key} = "{value}"'
        return True
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, f'{key} = "{value}"')
    return True


def _ensure_section(lines: list[str], section: str) -> None:
    if _section_span(lines, section) is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines.append(f"[{section}]")


def _all_sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """按出现顺序返回 [(节名, 节头行号, 节结束行号)]。"""
    headers: list[tuple[str, int]] = []
    for index, raw in enumerate(lines):
        match = _SECTION_RE.match(raw.strip())
        if match:
            headers.append((match.group(1).strip(), index))
    result: list[tuple[str, int, int]] = []
    for position, (name, start) in enumerate(headers):
        end = headers[position + 1][1] if position + 1 < len(headers) else len(lines)
        result.append((name, start, end))
    return result


def _update_keys(lines: list[str], mapping: dict[str, str]) -> list[str]:
    """把非空值写进**所有**已有同名键位置；完全没有时补到 [GENERAL]/[FLTSIM]。

    A380 等模板把 atc 字段放在 [FLTSIM]，A350/PMDG 放在 [General]/[GENERAL]；
    克隆时两处都要更新，避免旧注册号残留。
    """
    for key, value in mapping.items():
        if not value:
            continue
        placed = False
        for _name, start, end in _all_sections(lines):
            index = _find_key(lines, start, end, key)
            if index is None:
                continue
            match = _KEY_RE.match(lines[index].strip())
            original_key = match.group(1).strip() if match else key
            lines[index] = f'{original_key} = "{value}"'
            placed = True
        if placed:
            continue
        if _section_span(lines, "general") is not None:
            target = "general"
        elif _section_span(lines, "fltsim") is not None:
            target = "fltsim"
        else:
            _ensure_section(lines, "GENERAL")
            target = "general"
        _set_value(lines, target, key, value)
    return lines


def update_livery_cfg(text: str, info: LiveryInfo,
                      *, atc_fields: bool = True) -> str:
    """克隆模式：更新 livery.cfg 的 name/atc_*/ui_variation。

    保留其它节与键（含 [Selection]、[Panel_DynamicParameters]、注释）。
    只写入非空的新值；空值保留模板原值。``ui_variation`` 模板里有就更新、
    没有则补一个（默认等于 title）。
    """
    data = info.normalized()
    lines = _split_lines(text)
    mapping = {
        "name": data.title,
        "ui_variation": data.ui_variation,
    }
    if atc_fields:
        mapping.update({
            "atc_id": data.atc_id,
            "atc_airline": data.atc_airline,
            "icao_airline": data.icao_airline,
        })
    lines = _update_keys(lines, mapping)
    return "\r\n".join(lines).rstrip("\r\n")


def update_aircraft_cfg(text: str, info: LiveryInfo,
                        texture_code: str | None = None) -> str:
    """克隆模式：更新 2020 legacy aircraft.cfg 的 [FLTSIM.0] 关键字段。"""
    data = info.normalized()
    lines = _split_lines(text)
    fltsim_section = None
    for raw in lines:
        match = _SECTION_RE.match(raw.strip())
        if match and match.group(1).strip().lower().startswith("fltsim"):
            fltsim_section = match.group(1).strip()
            break
    if fltsim_section is None:
        fltsim_section = "FLTSIM.0"
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"[{fltsim_section}]"])
    mapping = {
        "title": data.title,
        "ui_variation": data.ui_variation,
        "texture": texture_code or "",
        "atc_id": data.atc_id,
        "atc_airline": data.atc_airline,
        "icao_airline": data.icao_airline,
    }
    for key, value in mapping.items():
        if value:
            _set_value(lines, fltsim_section, key, value)
    return "\r\n".join(lines).rstrip("\r\n")


__all__ = [
    "AIRCRAFT_CFG",
    "DEFAULT_AIRCRAFT_CFG_TEMPLATE",
    "DEFAULT_LIVERY_CFG_TEMPLATE",
    "FLTSIM_COMMENT",
    "LIVERY_CFG",
    "TEXTURE_CFG",
    "build_aircraft_cfg",
    "build_livery_cfg",
    "build_texture_cfg",
    "cfg_values",
    "parse_cfg",
    "render_cfg_template",
    "update_aircraft_cfg",
    "update_livery_cfg",
    "write_aircraft_cfg",
    "write_livery_cfg",
    "write_texture_cfg",
]
