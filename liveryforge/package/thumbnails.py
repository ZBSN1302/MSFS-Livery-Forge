"""缩略图生成。

真实样本（E:\\历史\\Community 实测）：
- 2024：``thumbnail/thumbnail.png``、``thumbnail_small.png`` 为主；
  常见尺寸 720x344 / 600x216，A380 为 1618x582，另有 1920x1080、2000x956；
  可选 ``thumbnail_side.png``（930x340）、``thumbnail_button.png``（830x260）、
  ``thumbnail_popup.png``（1400x1400）。
- 2020 legacy：``TEXTURE.<X>/thumbnail.jpg``（1618x582）+ ``thumbnail_small.jpg``
  （600x216），JPEG。
- 策略：按 profile.thumbnail_sizes 里的“长边像素”等比缩放，绝不拉伸；
  无用户截图时生成品牌色占位图（中/英文标题，字体多级回退，绝不因缺字体崩溃）。
"""
from __future__ import annotations

from pathlib import Path

from liveryforge.types import LiveryForgeError

try:  # Pillow 在运行环境可用；延迟报错便于无依赖时给出中文提示
    from PIL import Image, ImageDraw, ImageFont
except Exception as _PIL_ERROR:  # pragma: no cover - 仅在缺 Pillow 时
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]

THUMBNAIL_2024_DEFAULT: dict[str, int] = {
    "thumbnail.png": 1600,
    "thumbnail_small.png": 600,
    "thumbnail_side.png": 930,
    "thumbnail_button.png": 830,
    "thumbnail_popup.png": 1400,
}
THUMBNAIL_2020_DEFAULT: dict[str, int] = {
    "thumbnail.jpg": 1600,
    "thumbnail_small.jpg": 600,
}

BRAND_COLOR = (18, 52, 92)
BRAND_ACCENT = (0, 160, 220)

#: 字体回退顺序：中文优先，其次英文，最后 Pillow 内置字体
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "DejaVuSans.ttf",
)


def _require_pillow() -> None:
    if Image is None:
        raise LiveryForgeError(
            "缺少 Pillow 库，无法生成缩略图。请安装 Pillow 或使用便携运行环境。")


def _load_font(size: int):
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:  # 旧版 Pillow 没有 size 参数
        try:
            return ImageFont.load_default()
        except Exception:
            return None


def fit_size(width: int, height: int, long_edge: int) -> tuple[int, int]:
    """等比缩放到指定长边（不放大；长边小于目标时保持原尺寸）。"""
    width, height = int(width), int(height)
    long_edge = int(long_edge)
    if width <= 0 or height <= 0 or long_edge <= 0:
        return max(width, 1), max(height, 1)
    current = max(width, height)
    if current <= long_edge:
        return width, height
    if width >= height:
        return long_edge, max(1, round(height * long_edge / width))
    return max(1, round(width * long_edge / height)), long_edge


def _gradient_background(size: tuple[int, int], color: tuple[int, int, int]):
    width, height = size
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        shade = tuple(int(c * (0.55 + 0.45 * (1 - ratio))) for c in color)
        draw.line([(0, y), (width, y)], fill=shade)
    draw.rectangle([0, int(height * 0.82), width, height], fill=BRAND_ACCENT)
    return image


def _draw_centered_text(draw, text: str, box: tuple[int, int], font_size: int) -> None:
    width, height = box
    size = max(12, int(font_size))
    while size > 10:
        font = _load_font(size)
        if font is None:
            return
        try:
            left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        except Exception:
            return
        text_width, text_height = right - left, bottom - top
        if text_width <= width * 0.9 and text_height <= height * 0.6:
            draw.text(((width - text_width) / 2 - left,
                       (height - text_height) / 2 - top),
                      text, font=font, fill=(255, 255, 255))
            return
        size = int(size * 0.85)


def create_placeholder(size: tuple[int, int], title: str,
                       subtitle: str = "MSFS Livery Forge") -> "Image.Image":
    """生成品牌色占位图（中/英文标题均可，缺字体不崩溃）。"""
    _require_pillow()
    width, height = size
    image = _gradient_background((max(2, width), max(2, height)), BRAND_COLOR)
    draw = ImageDraw.Draw(image)
    _draw_centered_text(draw, title or "Livery", (width, int(height * 0.9)),
                        int(min(height * 0.28, width * 0.12)) or 24)
    _draw_centered_text(draw, subtitle, (width, int(height * 0.18)),
                        max(10, int(height * 0.06)))
    return image


def _render_from_source(source: Path, size: tuple[int, int]):
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    resample = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)
    return image.resize(size, resample)


def _save(image, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        image.save(target, format="JPEG", quality=92, optimize=True)
    else:
        image.save(target, format="PNG", optimize=True)
    return target


def _fit_contain(image, canvas_size: tuple[int, int]):
    """把图片等比缩放并居中放到 canvas 上（不裁剪、不拉伸）。"""
    canvas_w, canvas_h = canvas_size
    ratio = min(canvas_w / max(1, image.width), canvas_h / max(1, image.height))
    width = max(1, int(round(image.width * ratio)))
    height = max(1, int(round(image.height * ratio)))
    resample = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)
    resized = image.resize((width, height), resample)
    canvas = Image.new("RGB", canvas_size, (12, 20, 30))
    canvas.paste(resized, ((canvas_w - width) // 2, (canvas_h - height) // 2))
    return canvas


def generate_thumbnails(source: Path | None,
                        out_dir: Path,
                        *,
                        title: str,
                        sizes: dict[str, int] | None = None,
                        generation: str = "2024",
                        subtitle: str = "MSFS Livery Forge",
                        progress=None) -> list[Path]:
    """生成一组缩略图，返回写出的文件列表。

    :param source: 用户截图；None/不可读时退化为占位图
    :param out_dir: 输出目录（2024 为 livery/thumbnail，2020 为 TEXTURE.<X>）
    :param sizes: {文件名: 长边像素}；None 时按 generation 用默认（1600/600）
    """
    _require_pillow()
    out_dir = Path(out_dir)
    if not sizes:
        sizes = dict(THUMBNAIL_2020_DEFAULT if str(generation) == "2020"
                     else THUMBNAIL_2024_DEFAULT)
    source = Path(source) if source else None
    base_image = None
    if source is not None:
        try:
            with Image.open(source) as opened:
                base_image = opened.convert("RGB")
        except Exception:
            base_image = None

    written: list[Path] = []
    main_long_edge = max((int(v) for v in sizes.values() if int(v) > 0), default=1600) or 1600
    for name, long_edge in sizes.items():
        long_edge = int(long_edge)
        if "popup" in name.lower():
            # 真实包 thumbnail_popup.png 为正方形：等比 contain 到正方形画布
            canvas = (max(long_edge, 64), max(long_edge, 64))
            if base_image is not None:
                image = _fit_contain(base_image, canvas)
            else:
                image = create_placeholder(canvas, title, subtitle)
        elif base_image is not None:
            size = fit_size(base_image.width, base_image.height, long_edge)
            image = base_image.resize(
                size, getattr(Image.Resampling, "LANCZOS", Image.LANCZOS))
        else:
            size = fit_size(main_long_edge * 2, main_long_edge, long_edge)
            image = create_placeholder(size, title, subtitle)
        written.append(_save(image, out_dir / name))
        if progress is not None:
            try:
                progress(f"生成缩略图 {name}", None)
            except Exception:
                pass
    return written


def template_thumbnail_sizes(template_dir: Path) -> dict[str, int]:
    """读取模板涂装 thumbnail 目录下已有缩略图，返回 {文件名: 长边}。

    克隆模式用：保留模板使用但 profile 未声明的缩略图文件名（如 A380 的
    ``thumbnail-small.png``），避免把模板的 5 张缩略图缩成只剩 2 张。
    """
    if Image is None:
        return {}
    result: dict[str, int] = {}
    directory = Path(template_dir)
    if not directory.is_dir():
        return result
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        try:
            with Image.open(path) as opened:
                result[path.name] = max(opened.size)
        except Exception:
            continue
    return result


def thumbnail_sizes_for(generation: str,
                        sizes: dict[str, int] | None = None) -> dict[str, int]:
    """返回实际使用的 {文件名: 长边} 配置（供 UI/测试查询）。"""
    if sizes:
        return dict(sizes)
    return dict(THUMBNAIL_2020_DEFAULT if str(generation) == "2020"
                else THUMBNAIL_2024_DEFAULT)


__all__ = [
    "BRAND_COLOR",
    "FONT_CANDIDATES",
    "THUMBNAIL_2020_DEFAULT",
    "THUMBNAIL_2024_DEFAULT",
    "create_placeholder",
    "fit_size",
    "generate_thumbnails",
    "template_thumbnail_sizes",
    "thumbnail_sizes_for",
]
