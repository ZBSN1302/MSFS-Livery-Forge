"""texconv 定位与调用封装。

只调用仓库自带的 ``tools/texconv/texconv.exe``（DirectXTex 2026.5.8.1），
不依赖系统 PATH，不联网。子进程的 stdout/stderr 重定向到临时文件而不是管道，
避免 Windows 沙箱/杀软环境下的管道问题，也便于失败时把最后几行日志带进中文错误。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ..paths import texconv_path
from ..types import TextureError

#: 允许的 texconv 格式（同时接受小写别名）。
FORMAT_ALIASES: dict[str, str] = {
    "bc1": "BC1_UNORM",
    "bc1_unorm": "BC1_UNORM",
    "bc3": "BC3_UNORM",
    "bc3_unorm": "BC3_UNORM",
    "bc4": "BC4_UNORM",
    "bc4_unorm": "BC4_UNORM",
    "bc5": "BC5_SNORM",
    "bc5_snorm": "BC5_SNORM",
    "bc7": "BC7_UNORM",
    "bc7_unorm": "BC7_UNORM",
}

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def resolve_format(fmt: str) -> str:
    """把 ``"bc7"`` / ``"BC7_UNORM"`` 统一为 texconv 的 ``-f`` 格式名。"""
    key = str(fmt).strip()
    resolved = FORMAT_ALIASES.get(key.lower())
    if resolved is None:
        raise TextureError(f"不支持的压缩格式：{fmt!r}（支持 BC1/BC3/BC4/BC5/BC7）")
    return resolved


def find_texconv(explicit: str | Path | None = None) -> Path:
    """定位 texconv.exe：显式路径优先，否则使用内置工具目录。"""
    if explicit is not None:
        path = Path(explicit)
        if path.is_dir():
            path = path / "texconv.exe"
        if not path.is_file():
            raise TextureError(f"指定的 texconv 不存在：{path}")
        return path
    path = texconv_path()
    if not path.is_file():
        raise TextureError(
            f"未找到内置 texconv：{path}\n"
            "请确认 tools/texconv/texconv.exe 存在，或在选项中指定 texconv 路径。"
        )
    return path


def _find_output(out_dir: Path, src: Path, started_at: float) -> Path | None:
    expected = out_dir / (src.stem + ".dds")
    if expected.is_file():
        return expected
    candidates = [p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() == ".dds"]
    if len(candidates) == 1:
        return candidates[0]
    fresh = [p for p in candidates if p.stat().st_mtime >= started_at - 1.0]
    if len(fresh) == 1:
        return fresh[0]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


def convert_to_dds(src: str | Path, fmt: str, out_dir: str | Path, *,
                   mipmaps: bool = True,
                   texconv: str | Path | None = None,
                   timeout: int = 300,
                   pow2: bool = False,
                   dx10: bool = True,
                   sepalpha: bool = False,
                   extra_args: list[str] | None = None) -> Path:
    """调用 texconv 把图片转换为 DDS，返回生成的 .dds 路径。

    - ``out_dir`` 会被自动创建（texconv 要求输出目录已存在）。
    - ``-m 0`` 生成完整 mip 链；``mipmaps=False`` 时用 ``-m 1`` 只保留 level0。
    - ``pow2=True`` 时加 ``-pow2``（非 2 的幂输入自动缩放，调用方应给出警告）。
    - 超时/退出码非 0/找不到输出文件都会抛 :class:`~liveryforge.types.TextureError`。
    """
    src_path = Path(src)
    if not src_path.is_file():
        raise TextureError(f"找不到输入图片：{src_path}")
    exe = find_texconv(texconv)
    fmt_name = resolve_format(fmt)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        str(exe),
        "-f", fmt_name,
        "-m", "0" if mipmaps else "1",
        "-y",
        "-o", str(out_path),
    ]
    if dx10:
        cmd.append("-dx10")
    if pow2:
        cmd.append("-pow2")
    if sepalpha:
        cmd.append("-sepalpha")
    if extra_args:
        cmd.extend(str(a) for a in extra_args)
    cmd.append(str(src_path))

    started_at = time.time()
    # 不用 tempfile.mkstemp：在部分 Windows 沙箱/受控环境下临时目录 ACL 异常会导致卡死，
    # 直接在工作目录（调用方保证可写）里建日志文件即可。
    log_path = out_path / f".texconv_{os.getpid()}_{time.time_ns()}.log"
    try:
        try:
            with open(log_path, "wb") as log_fh:
                completed = subprocess.run(
                    cmd,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    creationflags=_CREATE_NO_WINDOW,
                )
        except subprocess.TimeoutExpired as exc:
            raise TextureError(
                f"texconv 处理超时（{timeout} 秒）：{src_path.name}"
            ) from exc
        except OSError as exc:
            raise TextureError(f"无法启动 texconv（{exe}）：{exc}") from exc

        log_text = ""
        try:
            log_text = log_path.read_bytes().decode("utf-8", "replace")
        except OSError:
            pass

        if completed.returncode != 0:
            tail = "\n".join(log_text.strip().splitlines()[-8:])
            raise TextureError(
                f"texconv 转换失败（退出码 {completed.returncode}）：{src_path.name}"
                + (f"\n{tail}" if tail else "")
            )

        output = _find_output(out_path, src_path, started_at)
        if output is None:
            tail = "\n".join(log_text.strip().splitlines()[-8:])
            raise TextureError(
                f"texconv 未生成 DDS 输出：{src_path.name}" + (f"\n{tail}" if tail else "")
            )
        return output
    finally:
        try:
            log_path.unlink()
        except OSError:
            pass
