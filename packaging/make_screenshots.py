"""生成文档用的 GUI 截图（T3 开发辅助脚本，不随产品运行）。

用法::

    python packaging\\make_screenshots.py

产物写入 ``docs/images/``（README/quickstart 引用）。
需要 Windows 桌面会话；无显示环境会报错退出，不影响主程序。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "docs" / "images"


def settle(app, seconds: float) -> None:
    """在等待后台线程/事件回调时持续刷新界面。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            app.update()
        except Exception:  # noqa: BLE001 - 窗口已关闭
            return
        time.sleep(0.03)


def grab(app, filename: str) -> None:
    from PIL import ImageGrab

    app.deiconify()
    app.attributes("-topmost", True)
    app.lift()
    app.focus_force()
    settle(app, 0.5)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    width, height = app.winfo_width(), app.winfo_height()
    # 底部裁掉几像素，避免把窗口边框/任务栏边缘截进文档图片
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height - 8))
    path = OUT_DIR / filename
    image.save(path, format="PNG", optimize=True)
    print(f"已保存 {path} ({width}x{height}, {path.stat().st_size / 1024:.0f} KB)")


def main() -> int:
    try:
        from liveryforge.gui.app import LiveryForgeApp
    except Exception as exc:  # noqa: BLE001
        print(f"导入 GUI 失败：{exc}", file=sys.stderr)
        return 1
    if OUT_DIR.is_dir() is False:
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        app = LiveryForgeApp()
    except Exception as exc:  # noqa: BLE001 - 无显示环境
        print(f"无法打开 GUI（需要桌面会话）：{exc}", file=sys.stderr)
        return 1

    # 固定窗口位置，避免窗口底部被任务栏遮住导致截图异常
    app.geometry("1180x800+80+40")

    try:
        settle(app, 2.2)
        grab(app, "gui-main.png")

        app.notebook.select(0)
        app._show_step(1)
        settle(app, 0.6)
        grab(app, "gui-step2.png")

        app._show_step(2)
        settle(app, 0.5)
        grab(app, "gui-step3.png")

        app.notebook.select(1)
        settle(app, 0.6)
        grab(app, "gui-convert.png")

        app.notebook.select(2)
        settle(app, 0.6)
        grab(app, "gui-settings.png")

        app.notebook.select(3)
        settle(app, 0.5)
        grab(app, "gui-help.png")
    finally:
        try:
            app.destroy()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
