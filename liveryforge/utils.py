"""通用小工具：原子写入、文件名清理、Windows FILETIME、大小格式化。"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, fallback: str = "untitled") -> str:
    """把任意字符串变成合法的 Windows 文件名（不含路径分隔符）。"""
    cleaned = _INVALID_CHARS.sub("_", name).strip().strip(".")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:120]


def sanitize_dir_name(name: str, fallback: str = "livery") -> str:
    return sanitize_filename(name, fallback=fallback)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8",
                      newline: str | None = None) -> None:
    if newline is not None:
        text = text.replace("\r\n", "\n").replace("\n", newline)
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, obj, *, indent: int | None = 2,
                      ensure_ascii: bool = False) -> None:
    text = json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii)
    atomic_write_text(path, text, encoding="utf-8")


def unix_to_filetime(timestamp: float) -> int:
    """Unix 秒 -> Windows FILETIME（1601-01-01 起的 100ns）。"""
    return int(round((timestamp + 11644473600.0) * 10_000_000))


def filetime_for_path(path: Path) -> int:
    """文件 mtime 转 FILETIME，使用 ns 精度（Windows 上 100ns 对齐）。"""
    return path.stat().st_mtime_ns // 100 + 116444736000000000


def filetime_now() -> int:
    return time.time_ns() // 100 + 116444736000000000


def filetime_to_datetime(filetime: int) -> datetime:
    return datetime.fromtimestamp(filetime / 10_000_000 - 11644473600.0, tz=timezone.utc)


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{num} B"


def sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def log_line(log: list[str], message: str) -> None:
    log.append(message)
