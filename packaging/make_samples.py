"""生成 samples/ 目录下的示例贴图（T3 开发辅助脚本，不随产品运行）。

用法::

    python packaging\\make_samples.py

产物：

* ``samples/textures/EXAMPLE_FUSELAGE_ALBD.PNG`` 1024×1024 RGBA（含透明区域）
* ``samples/textures/EXAMPLE_FUSELAGE_COMP.PNG`` 1024×1024 RGB
* ``samples/textures/EXAMPLE_FUSELAGE_NORM.PNG`` 1024×1024 RGB
* ``samples/thumbnail_example.png`` 1920×1080 16:9 占位截图

只依赖 Pillow（项目自带运行时已包含）。
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "samples"
TEXTURE_DIR = SAMPLES_DIR / "textures"

# Windows 常见中文字体（找不到就退回 Pillow 内置字体，仅影响截图中文）
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)


def _font(size: int, bold: bool = False):
    candidates = _FONT_CANDIDATES[::-1] if not bold else _FONT_CANDIDATES
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (size, size), (*top, 255))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (size, y)], fill=(*color, 255))
    return image


def make_albedo(size: int = 1024) -> Image.Image:
    """示例机身底色贴图：蓝底、白色涂装带、橙色尾翼色块，并留出透明区域。"""
    image = _vertical_gradient(size, (24, 78, 148), (66, 132, 196))
    draw = ImageDraw.Draw(image)

    # 机身主色带（白色斜带）
    draw.polygon(
        [
            (0, int(size * 0.30)),
            (size, int(size * 0.16)),
            (size, int(size * 0.46)),
            (0, int(size * 0.60)),
        ],
        fill=(245, 248, 252, 255),
    )
    # 蓝色细线
    draw.polygon(
        [
            (0, int(size * 0.62)),
            (size, int(size * 0.48)),
            (size, int(size * 0.52)),
            (0, int(size * 0.66)),
        ],
        fill=(20, 58, 120, 255),
    )
    # 尾翼色块（橙色）
    draw.polygon(
        [
            (int(size * 0.72), 0),
            (size, 0),
            (size, int(size * 0.22)),
            (int(size * 0.80), int(size * 0.22)),
        ],
        fill=(232, 116, 28, 255),
    )
    # 圆形徽标
    draw.ellipse(
        [
            int(size * 0.08),
            int(size * 0.68),
            int(size * 0.34),
            int(size * 0.94),
        ],
        fill=(250, 250, 250, 255),
        outline=(20, 58, 120, 255),
        width=max(2, size // 160),
    )
    draw.ellipse(
        [
            int(size * 0.14),
            int(size * 0.74),
            int(size * 0.28),
            int(size * 0.88),
        ],
        fill=(232, 116, 28, 255),
    )
    # 文字（英文，避免示例贴图依赖中文字体）
    draw.text((int(size * 0.10), int(size * 0.40)), "SAMPLE LIVERY",
              font=_font(max(24, size // 14), bold=True), fill=(20, 58, 120, 255))
    draw.text((int(size * 0.10), int(size * 0.50)), "MSFS 2024  ·  LiveryForge",
              font=_font(max(16, size // 30)), fill=(20, 58, 120, 255))

    # 透明区域：模拟需要 alpha 的贴花/窗户，让转换流程能识别到 HasTransp
    draw.rectangle(
        [int(size * 0.60), int(size * 0.66), int(size * 0.78), int(size * 0.80)],
        fill=(0, 0, 0, 0),
    )
    draw.text((int(size * 0.61), int(size * 0.825)), "ALPHA AREA",
              font=_font(max(14, size // 40)), fill=(255, 255, 255, 180))
    return image


def make_comp(size: int = 1024) -> Image.Image:
    """示例材质贴图：R=粗糙度、G=金属度、B=环境光遮蔽（MSFS ORM 约定）。"""
    image = Image.new("RGB", (size, size), (128, 0, 255))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        t = y / max(1, size - 1)
        roughness = int(60 + 150 * t)          # 上部更亮（更粗糙）
        metal = 200 if (int(size * 0.30) < y < int(size * 0.46)) else 10
        ao = int(220 - 60 * abs(t - 0.5))
        draw.line([(0, y), (size, y)], fill=(roughness, metal, ao))
    # 金属条纹
    for index in range(6):
        top = int(size * (0.52 + index * 0.055))
        draw.rectangle(
            [int(size * 0.06), top, int(size * 0.94), top + max(2, size // 90)],
            fill=(90, 235, 210),
        )
    draw.text((int(size * 0.06), int(size * 0.90)), "COMP  R=Roughness G=Metal B=AO",
              font=_font(max(14, size // 42)), fill=(20, 20, 20))
    return image


def make_normal(size: int = 1024) -> Image.Image:
    """示例法线贴图：以 (128,128,255) 平面为基准叠加横向波纹。"""
    image = Image.new("RGB", (size, size), (128, 128, 255))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        phase = math.sin(2 * math.pi * y / (size / 16.0))
        red = int(128 + 26 * phase)
        green = int(128 - 18 * phase)
        blue = int(250 - 12 * abs(phase))
        draw.line([(0, y), (size, y)], fill=(red, green, blue))
    # 局部凸起（圆形法线）
    for cx, cy, radius in (
        (int(size * 0.30), int(size * 0.72), int(size * 0.10)),
        (int(size * 0.70), int(size * 0.30), int(size * 0.08)),
    ):
        for step in range(24):
            ratio = 1 - step / 24.0
            value = int(128 + 60 * ratio)
            draw.ellipse(
                [cx - radius * step // 24, cy - radius * step // 24,
                 cx + radius * step // 24, cy + radius * step // 24],
                outline=(value, value, 255),
            )
    draw.text((int(size * 0.06), int(size * 0.94)), "NORMAL  flat(128,128,255)",
              font=_font(max(14, size // 42)), fill=(30, 30, 30))
    return image


def make_thumbnail(width: int = 1920, height: int = 1080) -> Image.Image:
    """16:9 占位截图，供没有截图的新手直接体验「自定义缩略图」。"""
    image = Image.new("RGB", (width, height), (18, 26, 48))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        color = (
            int(18 + 40 * t),
            int(26 + 52 * t),
            int(48 + 90 * t),
        )
        draw.line([(0, y), (width, y)], fill=color)
    # 简易飞机剪影
    draw.polygon(
        [
            (int(width * 0.18), int(height * 0.56)),
            (int(width * 0.62), int(height * 0.44)),
            (int(width * 0.80), int(height * 0.40)),
            (int(width * 0.84), int(height * 0.47)),
            (int(width * 0.66), int(height * 0.52)),
            (int(width * 0.58), int(height * 0.56)),
            (int(width * 0.30), int(height * 0.62)),
        ],
        fill=(235, 240, 248),
    )
    draw.polygon(
        [
            (int(width * 0.46), int(height * 0.50)),
            (int(width * 0.70), int(height * 0.44)),
            (int(width * 0.72), int(height * 0.58)),
            (int(width * 0.50), int(height * 0.56)),
        ],
        fill=(232, 116, 28),
    )
    draw.text(
        (int(width * 0.06), int(height * 0.10)),
        "示例缩略图 / SAMPLE THUMBNAIL",
        font=_font(max(28, int(width / 28)), bold=True),
        fill=(240, 244, 252),
    )
    draw.text(
        (int(width * 0.06), int(height * 0.18)),
        "MSFS 2024 涂装工坊 · 16:9 占位图",
        font=_font(max(20, int(width / 48))),
        fill=(180, 200, 230),
    )
    return image


def main() -> int:
    TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    outputs = {
        TEXTURE_DIR / "EXAMPLE_FUSELAGE_ALBD.PNG": make_albedo(),
        TEXTURE_DIR / "EXAMPLE_FUSELAGE_COMP.PNG": make_comp(),
        TEXTURE_DIR / "EXAMPLE_FUSELAGE_NORM.PNG": make_normal(),
        SAMPLES_DIR / "thumbnail_example.png": make_thumbnail(),
    }
    for path, image in outputs.items():
        if path.suffix.lower() == ".png" and "ALBD" in path.name:
            image.save(path, format="PNG", optimize=True)
        else:
            image.save(path, format="PNG", optimize=True)
        print(f"已生成 {path}  ({path.stat().st_size / 1024:.0f} KB)")
    print("samples/ 示例素材生成完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
