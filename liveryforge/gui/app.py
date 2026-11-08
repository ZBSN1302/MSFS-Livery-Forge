"""MSFS 2024 涂装工坊 —— Tkinter 图形界面主窗口（T3）。

设计原则：

* **界面不阻塞**：扫描贴图、转换、打包全部在后台线程执行，通过 ``queue`` +
  ``after`` 回到主线程更新界面；
* **永远能打开**：T1/T2 引擎未就绪、缺少 texconv、配置目录只读时，窗口照常显示，
  只是相关按钮给出中文提示；
* **三步流程**：① 选择机型 → ② 选择贴图 + 填涂装信息 → ③ 输出目录 + 开始生成；
* 另有独立的「贴图转换」页签和「设置」页签。

直接启动::

    python -m liveryforge.gui.app            # 推荐
    python liveryforge\\gui\\app.py           # 也支持（会自动把项目根加入 sys.path）
    python -m liveryforge.gui.app --smoke 5  # 启动 5 秒后自动关闭（自检/打包验证用）
"""
from __future__ import annotations

import argparse
import queue
import re
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

# 允许 `python liveryforge\gui\app.py` 这种直接运行方式
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from liveryforge.gui import services
from liveryforge.gui.widgets import LabeledValue, PathRow, RadioRow, ScrolledLog, ScrollableFrame
from liveryforge.paths import APP_DISPLAY_NAME, VERSION, default_output_dir
from liveryforge.settings import Settings, settings_dir, settings_path
from liveryforge.types import LiveryForgeError, LiveryInfo, Profile

DEFAULT_GEOMETRY = "1180x800"
MIN_WINDOW_WIDTH = 1100
MIN_WINDOW_HEIGHT = 760
POLL_INTERVAL_MS = 80
GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")
HELP_TEXT = """\
欢迎使用 MSFS 2024 涂装工坊！

【三种制作方式】
1. 内置机型（推荐新手）
   在「① 制作涂装」里选择机型 → 选贴图文件夹 → 填涂装名称 →「开始生成」。
2. 克隆现有涂装（适配任意机型、第三方机模）
   选择模式为「克隆现有涂装」，模板选 Community 里某个涂装包内的 livery 文件夹
   （里面必须有 livery.cfg），再选你的新贴图目录，即可生成同款结构的涂装包。
3. 只转换贴图
   到「② 贴图转换」页签，选贴图文件夹和输出目录，选择 KTX2 / DDS / 两者，
   点「开始转换」，得到可直接放进涂装包的 .KTX2 / .DDS 文件。

【贴图要求】
· 格式：PNG 或 TIF（建议 PNG）；
· 尺寸：2 的幂（512 / 1024 / 2048 / 4096），宽高最好相同；
· 命名：保留原始文件名，例如 FUSELAGE_ALBD.PNG、FUSELAGE_COMP.PNG、
  FUSELAGE_NORM.PNG，程序会根据文件名中的 ALBD/COMP/NORM 等关键字识别类型；
· 有透明区域的涂装贴图请保留 alpha 通道，不要合并背景。

【生成之后】
· 点「打开输出目录」查看生成的涂装包；
· 点「复制到 Community」（需先在「③ 设置」配置 Community 路径），
  然后启动 MSFS 2024，在机库中选择该涂装即可。

【遇到问题】
· 「设置」页签里有环境自检与 Community 自动探测按钮；
· 详细图文教程见 docs\\quickstart.md，贴图知识见 docs\\texture-guide.md；
· 示例贴图在 samples\\textures 目录，可以先拿它们练习。
"""


def _pick_font_family(root: tk.Misc) -> str:
    """优先使用 Windows 自带中文字体，避免中文显示成方块。"""
    try:
        families = set(tkfont.families(root))
    except tk.TclError:
        families = set()
    for name in ("Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei", "SimHei", "SimSun", "Segoe UI"):
        if name in families:
            return name
    return "TkDefaultFont"


def _human_size(num: int) -> str:
    return services.human_size(num)


class LiveryForgeApp(tk.Tk):
    """主窗口。"""

    # ------------------------------------------------------------------ 初始化
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self._queue: queue.Queue = queue.Queue()
        self._profiles: list[Profile] = []
        self._profile_by_label: dict[str, Profile] = {}
        self._texture_entries: list = []
        self._last_result = None
        self._last_package_dir: Path | None = None
        self._last_convert_dir: Path | None = None
        self._busy = False
        self._font = _pick_font_family(self)
        self._init_variables()
        self._init_window()
        self._init_style()
        self._build_ui()
        self._load_settings_into_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_INTERVAL_MS, self._poll_queue)
        self.after(120, self._startup_tasks)
        self._log("欢迎使用 MSFS 2024 涂装工坊！", "muted")
        self._log("提示：先「选机型」→「选贴图」→「开始生成」。第一次使用建议点右上角「帮助」页签。", "muted")

    def _init_variables(self) -> None:
        self.mode_var = tk.StringVar(value="profile")
        self.profile_var = tk.StringVar()
        self.clone_dir_var = tk.StringVar()
        self.texture_dir_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.atc_id_var = tk.StringVar()
        self.airline_var = tk.StringVar()
        self.icao_var = tk.StringVar()
        self.author_var = tk.StringVar()
        self.version_var = tk.StringVar(value="1.0")
        self.quality_var = tk.StringVar(value="high")
        self.formats_var = tk.StringVar(value="ktx2")
        self.thumbnail_var = tk.StringVar()
        self.copy_community_var = tk.BooleanVar(value=False)
        self.output_dir_var = tk.StringVar()
        self.overwrite_var = tk.BooleanVar(value=True)
        self.conv_src_var = tk.StringVar()
        self.conv_out_var = tk.StringVar()
        self.conv_format_var = tk.StringVar(value="ktx2")
        self.conv_quality_var = tk.StringVar(value="high")
        self.community_var = tk.StringVar()
        self.set_quality_var = tk.StringVar(value="high")
        self.set_format_var = tk.StringVar(value="ktx2")

    def _init_window(self) -> None:
        self.title(f"{APP_DISPLAY_NAME} v{VERSION}")
        geometry = self.settings.window_geometry
        if not geometry or not GEOMETRY_RE.match(geometry):
            geometry = DEFAULT_GEOMETRY
        try:
            self.geometry(geometry)
        except tk.TclError:
            self.geometry(DEFAULT_GEOMETRY)
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.option_add("*tearOff", False)

    def _init_style(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "winnative", "clam", "default"):
            try:
                if theme in style.theme_names():
                    style.theme_use(theme)
                    break
            except tk.TclError:
                continue
        base = (self._font, 10)
        self.option_add("*Font", base)
        style.configure(".", font=base)
        style.configure("Title.TLabel", font=(self._font, 20, "bold"))
        style.configure("Subtitle.TLabel", font=(self._font, 10), foreground="#555555")
        style.configure("StepOn.TLabel", font=(self._font, 11, "bold"), foreground="#0b5cad")
        style.configure("StepOff.TLabel", font=(self._font, 11), foreground="#888888")
        style.configure("Hint.TLabel", font=(self._font, 9), foreground="#666666")
        style.configure("Warn.TLabel", font=(self._font, 9), foreground="#b26a00")
        style.configure("Error.TLabel", font=(self._font, 9), foreground="#b00020")
        style.configure("Ok.TLabel", font=(self._font, 9), foreground="#0a7a26")
        style.configure("Accent.TButton", font=(self._font, 11, "bold"))
        style.configure("Big.TButton", font=(self._font, 11))
        style.configure("Treeview", font=(self._font, 9), rowheight=24)
        style.configure("Treeview.Heading", font=(self._font, 9, "bold"))
        style.configure("TLabelframe.Label", font=(self._font, 10, "bold"))
        style.configure("Status.TLabel", font=(self._font, 9), foreground="#555555")

    # ------------------------------------------------------------------ 界面搭建
    def _build_ui(self) -> None:
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)
        self.columnconfigure(0, weight=1)
        self._build_header()
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 6))
        self._build_wizard_tab()
        self._build_convert_tab()
        self._build_settings_tab()
        self._build_help_tab()
        self._build_status_bar()

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(18, 12, 18, 4))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=APP_DISPLAY_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="把 PNG 贴图变成 MSFS 2024 可用的 KTX2 / DDS 涂装包",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(16, 0), pady=(10, 0))
        ttk.Label(header, text=f"v{VERSION}", style="Hint.TLabel").pack(side="right", pady=(12, 0))

    # ---- 步骤指示 --------------------------------------------------------
    def _build_step_indicator(self, parent: tk.Misc) -> tk.Frame:
        bar = ttk.Frame(parent)
        steps = ("1  选择机型", "2  选择贴图", "3  输出与生成")
        self.step_labels: list[ttk.Label] = []
        for index, text in enumerate(steps):
            if index:
                ttk.Label(bar, text="  →  ", style="Hint.TLabel").pack(side="left")
            label = ttk.Label(bar, text=text, style="StepOff.TLabel", padding=(6, 4))
            label.pack(side="left")
            self.step_labels.append(label)
        return bar

    def _show_step(self, index: int) -> None:
        self._current_step = max(0, min(2, index))
        for frame in self.step_frames:
            frame.grid_remove()
        self.step_frames[self._current_step].grid(row=0, column=0, sticky="nsew")
        for i, label in enumerate(self.step_labels):
            label.configure(style="StepOn.TLabel" if i == self._current_step else "StepOff.TLabel")
        self.btn_back.configure(state="normal" if self._current_step > 0 else "disabled")
        self.btn_next.configure(
            text="下一步 →" if self._current_step < 2 else "最后一步：确认输出",
            state="normal" if self._current_step < 2 else "disabled",
        )
        if self._current_step == 2:
            self.nav_hint.configure(text="确认输出目录后点击下方「开始生成」按钮。")
        elif self._current_step == 1:
            self.nav_hint.configure(text="选择贴图文件夹并填写涂装名称，然后点「下一步」。")
        else:
            self.nav_hint.configure(text="选择「内置机型」或「克隆现有涂装」，然后点「下一步」。")

    # ---- 主流程页签 ------------------------------------------------------
    def _build_wizard_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(6, 8))
        self.notebook.add(tab, text="① 制作涂装")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        self._build_step_indicator(tab).grid(row=0, column=0, sticky="w", padx=6, pady=(0, 6))
        container = ttk.Frame(tab)
        container.grid(row=1, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.step_frames = [ttk.Frame(container) for _ in range(3)]
        for frame in self.step_frames:
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)

        self._build_step_aircraft(self.step_frames[0])
        self._build_step_textures(self.step_frames[1])
        self._build_step_output(self.step_frames[2])

        nav = ttk.Frame(tab)
        nav.grid(row=2, column=0, sticky="ew", padx=6, pady=(8, 0))
        self.btn_back = ttk.Button(nav, text="← 上一步", command=self._go_back)
        self.btn_back.pack(side="left")
        self.nav_hint = ttk.Label(nav, text="", style="Hint.TLabel")
        self.nav_hint.pack(side="left", padx=12)
        self.btn_next = ttk.Button(nav, text="下一步 →", style="Accent.TButton", command=self._go_next)
        self.btn_next.pack(side="right")
        self._show_step(0)

    def _build_step_aircraft(self, parent: ttk.Frame) -> None:
        scroll = ScrollableFrame(parent, padding=(6, 4))
        scroll.grid(row=0, column=0, sticky="nsew")
        body = scroll.inner
        body.columnconfigure(0, weight=1)

        mode_group = ttk.LabelFrame(body, text="第 1 步 · 选择制作模式", padding=12)
        mode_group.grid(row=0, column=0, sticky="ew")
        mode_group.columnconfigure(0, weight=1)
        ttk.Radiobutton(
            mode_group,
            text="使用内置机型（推荐新手：零配置，选机型即可）",
            value="profile",
            variable=self.mode_var,
            command=self._on_mode_changed,
        ).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Radiobutton(
            mode_group,
            text="克隆现有涂装（适配任意机型 / 第三方机模：以已安装涂装为模板）",
            value="clone",
            variable=self.mode_var,
            command=self._on_mode_changed,
        ).grid(row=1, column=0, sticky="w", pady=2)

        profile_group = ttk.LabelFrame(body, text="内置机型", padding=12)
        profile_group.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        profile_group.columnconfigure(1, weight=1)

        ttk.Label(profile_group, text="机型", width=10, anchor="w").grid(row=0, column=0, sticky="w")
        self.profile_combo = ttk.Combobox(
            profile_group, textvariable=self.profile_var, state="readonly", height=12
        )
        self.profile_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_profile_notes())
        self.btn_refresh_profiles = ttk.Button(
            profile_group, text="刷新机型", width=10, command=self._load_profiles_async
        )
        self.btn_refresh_profiles.grid(row=0, column=2, sticky="e")

        self.profile_hint = ttk.Label(
            profile_group, text="正在加载内置机型…", style="Hint.TLabel", wraplength=880, justify="left"
        )
        self.profile_hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.profile_notes = ttk.Label(
            profile_group, text="", style="Hint.TLabel", wraplength=880, justify="left"
        )
        self.profile_notes.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        clone_group = ttk.LabelFrame(body, text="克隆现有涂装", padding=12)
        clone_group.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        clone_group.columnconfigure(0, weight=1)
        self.clone_row = PathRow(
            clone_group,
            "模板涂装文件夹",
            self.clone_dir_var,
            kind="dir",
            on_change=lambda _value: self._on_mode_changed(),
        )
        self.clone_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            clone_group,
            text=(
                "请选择 Community 中某个涂装包里的 livery 文件夹（内含 livery.cfg），\n"
                "例如：E:\\历史\\Community\\livery-longitude-n233cl\\SimObjects\\Airplanes\\asobo_longitude\\liveries\\TimHH\\C700_N233CL"
            ),
            style="Hint.TLabel",
            justify="left",
            wraplength=880,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        tips = ttk.LabelFrame(body, text="小提示", padding=10)
        tips.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            tips,
            text=(
                "· 第一次使用建议先用内置机型 + samples\\textures 里的示例贴图练习一遍。\n"
                "· 不知道选哪个机型？打开 MSFS 2024 机库确认机型名称，再在「内置机型」下拉里找对应项。\n"
                "· 内置机型里没有你的机型时，用「克隆现有涂装」模式：找一个你已经安装的同机型涂装做模板即可。"
            ),
            style="Hint.TLabel",
            justify="left",
            wraplength=900,
        ).grid(row=0, column=0, sticky="w")
        body.rowconfigure(3, weight=1)
        scroll.bind_mousewheel()

    def _build_step_textures(self, parent: ttk.Frame) -> None:
        scroll = ScrollableFrame(parent, padding=(6, 2))
        scroll.grid(row=0, column=0, sticky="nsew")
        body = scroll.inner
        body.columnconfigure(0, weight=1)

        group = ttk.LabelFrame(body, text="第 2 步 · 选择贴图文件夹", padding=10)
        group.grid(row=0, column=0, sticky="ew")
        group.columnconfigure(0, weight=1)
        self.texture_row = PathRow(
            group,
            "贴图文件夹",
            self.texture_dir_var,
            kind="dir",
            on_change=self._on_texture_dir_chosen,
        )
        self.texture_row.grid(row=0, column=0, sticky="ew")
        self.btn_rescan = ttk.Button(group, text="重新扫描", width=10, command=self._rescan_textures)
        self.btn_rescan.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.texture_count_label = ttk.Label(group, text="尚未选择贴图文件夹。", style="Hint.TLabel")
        self.texture_count_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        columns = ("file", "kind", "dims", "size")
        tree_frame = ttk.Frame(body)
        tree_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.texture_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", height=5, selectmode="browse"
        )
        headings = {"file": ("文件名", 420), "kind": ("识别类型", 160), "dims": ("尺寸", 120), "size": ("大小", 100)}
        for key, (text, width) in headings.items():
            self.texture_tree.heading(key, text=text)
            self.texture_tree.column(key, width=width, anchor="w", stretch=(key == "file"))
        self.texture_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.texture_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.texture_tree.configure(yscrollcommand=scrollbar.set)

        self.texture_warn_label = ttk.Label(body, text="", style="Warn.TLabel", wraplength=1000, justify="left")
        self.texture_warn_label.grid(row=2, column=0, sticky="w", pady=(4, 0))

        form = ttk.LabelFrame(body, text="涂装信息（带 * 为必填）", padding=10)
        form.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for column in (1, 3):
            form.columnconfigure(column, weight=1)

        def entry_pair(row: int, col: int, label: str, variable: tk.StringVar, width: int = 26) -> ttk.Entry:
            ttk.Label(form, text=label, width=12, anchor="w").grid(row=row, column=col, sticky="w", pady=2)
            widget = ttk.Entry(form, textvariable=variable, width=width)
            widget.grid(row=row, column=col + 1, sticky="ew", padx=(0, 16), pady=2)
            return widget

        self.title_entry = entry_pair(0, 0, "涂装名称 *", self.title_var)
        entry_pair(0, 2, "注册号", self.atc_id_var)
        entry_pair(1, 0, "航空公司", self.airline_var)
        entry_pair(1, 2, "ICAO 三字码", self.icao_var)
        entry_pair(2, 0, "作者", self.author_var)
        entry_pair(2, 2, "版本", self.version_var)

        advanced = ttk.LabelFrame(body, text="高级选项", padding=10)
        advanced.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        advanced.columnconfigure(1, weight=1)
        RadioRow(
            advanced,
            "贴图质量",
            self.quality_var,
            (("高（推荐，文件更大更清晰）", "high"), ("标准（体积更小）", "standard")),
            label_width=12,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        RadioRow(
            advanced,
            "输出格式",
            self.formats_var,
            (("KTX2（MSFS 2024）", "ktx2"), ("DDS（2020/legacy）", "dds"), ("两者都要", "both")),
            label_width=12,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self.thumbnail_row = PathRow(
            advanced,
            "缩略图（可选）",
            self.thumbnail_var,
            kind="open",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg"), ("所有文件", "*.*")],
            label_width=12,
        )
        self.thumbnail_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Checkbutton(
            advanced,
            text="生成后顺便复制到 Community（需先在「设置」里配置 Community 路径）",
            variable=self.copy_community_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        scroll.bind_mousewheel()

    def _build_step_output(self, parent: ttk.Frame) -> None:
        body = ttk.Frame(parent, padding=(6, 4))
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(5, weight=1)

        group = ttk.LabelFrame(body, text="第 3 步 · 输出目录", padding=10)
        group.grid(row=0, column=0, sticky="ew")
        group.columnconfigure(0, weight=1)
        self.output_row = PathRow(group, "输出目录", self.output_dir_var, kind="dir")
        self.output_row.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(group, text="覆盖已存在的同名涂装包", variable=self.overwrite_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        action = ttk.Frame(body)
        action.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.btn_start = ttk.Button(
            action, text="⚙  开始生成涂装包", style="Accent.TButton", command=self._start_build, width=22
        )
        self.btn_start.pack(side="left")
        self.output_hint = ttk.Label(
            action, text="生成完成后，可打开输出目录或复制到 Community。", style="Hint.TLabel"
        )
        self.output_hint.pack(side="left", padx=12)

        progress_frame = ttk.Frame(body)
        progress_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        progress_frame.columnconfigure(0, weight=1)
        self.build_progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.build_progress.grid(row=0, column=0, sticky="ew")
        self.build_percent = ttk.Label(progress_frame, text="0%", width=6, anchor="e")
        self.build_percent.grid(row=0, column=1, sticky="e", padx=(6, 0))

        result_frame = ttk.Frame(body)
        result_frame.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.btn_open_output = ttk.Button(
            result_frame, text="打开输出目录", command=self._open_output_dir, state="disabled"
        )
        self.btn_open_output.pack(side="left")
        self.btn_copy_community = ttk.Button(
            result_frame, text="复制到 Community", command=self._copy_last_to_community, state="disabled"
        )
        self.btn_copy_community.pack(side="left", padx=8)
        self.result_label = ttk.Label(body, text="", style="Ok.TLabel", wraplength=1000, justify="left")
        self.result_label.grid(row=4, column=0, sticky="w", pady=(4, 0))

        log_frame = ttk.LabelFrame(body, text="运行日志", padding=6)
        log_frame.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.build_log = ScrolledLog(log_frame, height=10)
        self.build_log.grid(row=0, column=0, sticky="nsew")

    # ---- 贴图转换页签 ----------------------------------------------------
    def _build_convert_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.notebook.add(tab, text="② 贴图转换")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(5, weight=1)

        ttk.Label(
            tab,
            text="只转换贴图：把文件夹里的 PNG/TIF 批量转成 KTX2 / DDS，不生成涂装包。",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w")

        source_group = ttk.LabelFrame(tab, text="转换设置", padding=10)
        source_group.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        source_group.columnconfigure(0, weight=1)
        self.conv_src_row = PathRow(source_group, "贴图文件夹", self.conv_src_var, kind="dir")
        self.conv_src_row.grid(row=0, column=0, sticky="ew")
        self.conv_out_row = PathRow(source_group, "输出目录", self.conv_out_var, kind="dir")
        self.conv_out_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        RadioRow(
            source_group,
            "输出格式",
            self.conv_format_var,
            (("KTX2（MSFS 2024）", "ktx2"), ("DDS（2020/legacy）", "dds"), ("两者都要", "both")),
            label_width=12,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        RadioRow(
            source_group,
            "贴图质量",
            self.conv_quality_var,
            (("高（推荐）", "high"), ("标准", "standard")),
            label_width=12,
        ).grid(row=3, column=0, sticky="w", pady=(2, 0))

        action = ttk.Frame(tab)
        action.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.btn_convert = ttk.Button(
            action, text="开始转换", style="Accent.TButton", command=self._start_convert, width=16
        )
        self.btn_convert.pack(side="left")
        self.btn_open_convert = ttk.Button(
            action, text="打开输出目录", command=self._open_convert_dir, state="disabled"
        )
        self.btn_open_convert.pack(side="left", padx=8)

        progress_frame = ttk.Frame(tab)
        progress_frame.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        progress_frame.columnconfigure(0, weight=1)
        self.conv_progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.conv_progress.grid(row=0, column=0, sticky="ew")
        self.conv_percent = ttk.Label(progress_frame, text="0%", width=6, anchor="e")
        self.conv_percent.grid(row=0, column=1, sticky="e", padx=(6, 0))

        self.conv_status = ttk.Label(tab, text="选择贴图文件夹和输出目录后点击「开始转换」。", style="Hint.TLabel")
        self.conv_status.grid(row=4, column=0, sticky="w", pady=(4, 0))

        log_frame = ttk.LabelFrame(tab, text="转换日志", padding=6)
        log_frame.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.conv_log = ScrolledLog(log_frame, height=12)
        self.conv_log.grid(row=0, column=0, sticky="nsew")

    # ---- 设置页签 --------------------------------------------------------
    def _build_settings_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.notebook.add(tab, text="③ 设置")
        tab.columnconfigure(0, weight=1)

        community_group = ttk.LabelFrame(tab, text="Community 目录（复制涂装包的目标）", padding=10)
        community_group.grid(row=0, column=0, sticky="ew")
        community_group.columnconfigure(0, weight=1)
        self.community_row = PathRow(
            community_group, "Community 路径", self.community_var, kind="dir", label_width=14
        )
        self.community_row.grid(row=0, column=0, sticky="ew")
        buttons = ttk.Frame(community_group)
        buttons.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.btn_detect_community = ttk.Button(
            buttons, text="自动探测", command=lambda: self._detect_community(manual=True)
        )
        self.btn_detect_community.pack(side="left")
        ttk.Button(buttons, text="打开此目录", command=self._open_community_dir).pack(side="left", padx=8)
        ttk.Label(
            community_group,
            text="自动探测会读取 MSFS 的 UserCfg.opt；探测失败时可以手动选择 Community 文件夹。",
            style="Hint.TLabel",
            wraplength=960,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

        defaults_group = ttk.LabelFrame(tab, text="默认值", padding=10)
        defaults_group.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        defaults_group.columnconfigure(1, weight=1)
        ttk.Label(defaults_group, text="默认作者名", width=14, anchor="w").grid(row=0, column=0, sticky="w")
        ttk.Entry(defaults_group, textvariable=self.author_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 200)
        )
        RadioRow(
            defaults_group,
            "默认质量",
            self.set_quality_var,
            (("高", "high"), ("标准", "standard")),
            label_width=14,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        RadioRow(
            defaults_group,
            "默认格式",
            self.set_format_var,
            (("KTX2", "ktx2"), ("DDS", "dds"), ("两者都要", "both")),
            label_width=14,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        env_group = ttk.LabelFrame(tab, text="运行环境", padding=10)
        env_group.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        env_group.columnconfigure(1, weight=1)
        self.env_labels: dict[str, LabeledValue] = {}
        rows = (
            ("runtime", "Python 运行时"),
            ("texconv", "texconv 压缩工具"),
            ("texture", "贴图转换引擎"),
            ("builder", "涂装打包引擎"),
            ("clone", "克隆模式引擎"),
            ("profiles", "内置机型数据"),
        )
        for index, (key, label) in enumerate(rows):
            value = LabeledValue(env_group, label, "检查中…", label_width=18)
            value.grid(row=index, column=0, columnspan=3, sticky="w", pady=1)
            self.env_labels[key] = value
        ttk.Button(env_group, text="重新检测", command=self._refresh_engine_status_async).grid(
            row=len(rows), column=0, sticky="w", pady=(8, 0)
        )

        config_group = ttk.LabelFrame(tab, text="配置文件", padding=10)
        config_group.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        config_group.columnconfigure(0, weight=1)
        self.config_path_label = ttk.Label(
            config_group, text=str(settings_path()), style="Hint.TLabel", wraplength=1000, justify="left"
        )
        self.config_path_label.grid(row=0, column=0, sticky="w")
        config_buttons = ttk.Frame(config_group)
        config_buttons.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(config_buttons, text="保存设置", style="Accent.TButton", command=self._save_settings_clicked).pack(
            side="left"
        )
        ttk.Button(config_buttons, text="打开配置目录", command=self._open_config_dir).pack(side="left", padx=8)

    # ---- 帮助页签 --------------------------------------------------------
    def _build_help_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(10, 10))
        self.notebook.add(tab, text="帮助")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        container = ttk.Frame(tab)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        text = tk.Text(
            container,
            wrap="word",
            font=(self._font, 10),
            relief="flat",
            padx=12,
            pady=10,
            background=self.cget("background"),
        )
        text.insert("1.0", HELP_TEXT)
        text.configure(state="disabled")
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(tab)
        buttons.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="打开示例贴图目录", command=self._open_samples_dir).pack(side="left")
        ttk.Button(buttons, text="打开文档目录", command=self._open_docs_dir).pack(side="left", padx=8)
        ttk.Button(buttons, text="打开项目目录", command=self._open_project_dir).pack(side="left")

    # ---- 状态栏 ----------------------------------------------------------
    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self, padding=(16, 4, 16, 6))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.status_text = ttk.Label(bar, text="正在初始化…", style="Status.TLabel")
        self.status_text.grid(row=0, column=0, sticky="w")
        self.status_engine = ttk.Label(bar, text="", style="Status.TLabel")
        self.status_engine.grid(row=0, column=1, sticky="e")
        ttk.Button(bar, text="环境自检", command=self._refresh_engine_status_async).grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )

    # ------------------------------------------------------------------ 启动任务
    def _startup_tasks(self) -> None:
        config_note = ""
        try:
            settings_file = settings_path()
            config_note = f"配置文件：{settings_file}"
        except OSError as exc:
            config_note = f"配置文件路径不可用：{exc}"
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        runtime_root = Path(sys.executable).resolve().parent
        portable = runtime_root.name.lower() == "runtime"
        self.status_text.configure(
            text=f"Python {python_version}{'（便携运行时）' if portable else ''} · {config_note}"
        )
        self._refresh_engine_status_async()
        self._load_profiles_async()
        self._detect_community(manual=False)
        self._on_mode_changed()

    # ------------------------------------------------------------------ 队列轮询
    def _post(self, kind: str, *payload) -> None:
        self._queue.put((kind, payload))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                handler = getattr(self, f"_on_{kind}", None)
                if callable(handler):
                    try:
                        handler(*payload)
                    except tk.TclError:
                        return
                    except Exception:  # noqa: BLE001 - 界面回调异常不能中断轮询
                        traceback.print_exc()
        except queue.Empty:
            pass
        try:
            self.after(POLL_INTERVAL_MS, self._poll_queue)
        except tk.TclError:
            pass

    def _run_in_thread(self, target, *args, name: str = "liveryforge-worker") -> None:
        threading.Thread(target=target, args=args, name=name, daemon=True).start()

    # ------------------------------------------------------------------ 日志
    def _log(self, message: str, tag: str | None = None) -> None:
        if hasattr(self, "build_log"):
            self.build_log.append(message, tag)

    def _log_convert(self, message: str, tag: str | None = None) -> None:
        if hasattr(self, "conv_log"):
            self.conv_log.append(message, tag)

    # ------------------------------------------------------------------ 配置加载
    def _load_settings_into_ui(self) -> None:
        settings = self.settings
        self.community_var.set(settings.community_dir)
        self.author_var.set(settings.author)
        self.quality_var.set("high" if settings.quality_high else "standard")
        self.formats_var.set(settings.formats if settings.formats in ("ktx2", "dds", "both") else "ktx2")
        self.set_quality_var.set(self.quality_var.get())
        self.set_format_var.set(self.formats_var.get())
        self.copy_community_var.set(bool(settings.copy_to_community))
        self.clone_dir_var.set(settings.clone_template_dir)
        self.texture_dir_var.set(settings.last_texture_dir)
        self.thumbnail_var.set(settings.thumbnail_dir)
        output = settings.last_output_dir or str(default_output_dir())
        self.output_dir_var.set(output)
        self.conv_out_var.set(settings.last_convert_dir or str(Path(output) / "贴图转换"))

    def _collect_settings(self) -> Settings:
        current = self.settings
        current.community_dir = self.community_var.get().strip()
        current.author = self.author_var.get().strip()
        current.quality_high = self.set_quality_var.get() == "high"
        current.formats = self.set_format_var.get()
        current.last_output_dir = self.output_dir_var.get().strip()
        current.last_texture_dir = self.texture_dir_var.get().strip()
        current.last_convert_dir = self.conv_out_var.get().strip()
        current.clone_template_dir = self.clone_dir_var.get().strip()
        current.thumbnail_dir = self.thumbnail_var.get().strip()
        current.copy_to_community = bool(self.copy_community_var.get())
        current.window_geometry = self.geometry()
        return current

    def _persist_settings(self, show_error: bool = False) -> bool:
        try:
            self._collect_settings().save()
            self.settings = self._collect_settings()
            return True
        except OSError as exc:
            if show_error:
                messagebox.showwarning(
                    "设置未能保存",
                    f"无法写入配置文件：\n{exc}\n\n"
                    "本次设置只对当前窗口有效。\n"
                    "解决建议：检查文件是否被其它程序占用，或以普通用户身份运行为程序所在目录授权。",
                    parent=self,
                )
            return False

    def _save_settings_clicked(self) -> None:
        self._collect_settings()
        if self._persist_settings(show_error=True):
            messagebox.showinfo("设置已保存", "设置已保存，下次启动会自动加载。", parent=self)
            self._log("设置已保存。", "ok")

    # ------------------------------------------------------------------ 机型加载
    def _load_profiles_async(self) -> None:
        if getattr(self, "_loading_profiles", False):
            return
        self._loading_profiles = True
        self.profile_hint.configure(text="正在加载内置机型…", style="Hint.TLabel")
        self._run_in_thread(self._profiles_worker, name="profiles-loader")

    def _profiles_worker(self) -> None:
        try:
            profiles, error = services.load_profiles()
        except Exception as exc:  # noqa: BLE001
            profiles, error = [], f"{type(exc).__name__}: {exc}"
        self._post("profiles", profiles, error)

    def _on_profiles(self, profiles: list, error: str) -> None:
        self._loading_profiles = False
        self._profiles = list(profiles)
        self._profile_by_label = {services.format_profile_label(p): p for p in profiles}
        labels = list(self._profile_by_label.keys())
        self.profile_combo.configure(values=labels)
        if labels:
            current = self.profile_var.get()
            if current not in self._profile_by_label:
                self.profile_var.set(labels[0])
            self.profile_hint.configure(
                text=f"已加载 {len(labels)} 个内置机型。选好后点「下一步」。", style="Ok.TLabel"
            )
            self._update_profile_notes()
            self._log(f"已加载 {len(labels)} 个内置机型。", "muted")
        else:
            self.profile_var.set("")
            self.profile_hint.configure(
                text="暂无内置机型，可用克隆模式（选择已安装的涂装文件夹作为模板）。", style="Warn.TLabel"
            )
            if error:
                self._log(f"内置机型不可用：{error}", "warn")
            if self.mode_var.get() != "clone":
                self.mode_var.set("clone")
                self._on_mode_changed()

    def _on_profile_selected(self, _event=None) -> None:
        self._update_profile_notes()

    def _update_profile_notes(self) -> None:
        profile = self._profile_by_label.get(self.profile_var.get())
        if profile is None:
            self.profile_notes.configure(text="")
            return
        parts = []
        if profile.notes:
            parts.append(profile.notes)
        if profile.required_tags:
            parts.append(f"必需 tags：{profile.required_tags}")
        if profile.generation:
            parts.append(f"适用版本：{profile.generation}")
        self.profile_notes.configure(text="　".join(parts))

    # ------------------------------------------------------------------ 模式切换
    def _on_mode_changed(self) -> None:
        profile_mode = self.mode_var.get() != "clone"
        self.profile_combo.configure(state="readonly" if profile_mode else "disabled")
        self.btn_refresh_profiles.configure(state="normal" if profile_mode else "disabled")
        clone_state = "disabled" if profile_mode else "normal"
        for child in self.clone_row.winfo_children():
            try:
                child.configure(state=clone_state)
            except tk.TclError:
                pass
        self._sync_nav_hint()

    def _sync_nav_hint(self) -> None:
        if getattr(self, "_current_step", 0) != 0:
            return
        if self.mode_var.get() == "clone":
            self.nav_hint.configure(text="选择模板涂装文件夹后点「下一步」。")
        else:
            self.nav_hint.configure(text="选择内置机型后点「下一步」。")

    # ------------------------------------------------------------------ 贴图扫描
    def _on_texture_dir_chosen(self, directory: str) -> None:
        self._texture_entries = []
        self._fill_texture_tree([])
        if not directory or not Path(directory).is_dir():
            self.texture_count_label.configure(text="请选择有效的贴图文件夹。", style="Warn.TLabel")
            return
        self.texture_count_label.configure(text=f"正在扫描：{directory} …", style="Hint.TLabel")
        self._run_in_thread(self._scan_worker, directory, name="texture-scanner")

    def _rescan_textures(self) -> None:
        directory = self.texture_dir_var.get().strip()
        if not directory:
            messagebox.showinfo("请先选择文件夹", "请先选择贴图文件夹，再点「重新扫描」。", parent=self)
            return
        self._on_texture_dir_chosen(directory)

    def _scan_worker(self, directory: str) -> None:
        try:
            entries, warnings = services.scan_texture_dir(directory)
        except LiveryForgeError as exc:
            self._post("scan_error", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._post("scan_error", f"扫描贴图失败：{type(exc).__name__}: {exc}")
            return
        self._post("scan_done", entries, warnings, directory)

    def _on_scan_error(self, message: str) -> None:
        self.texture_count_label.configure(text="扫描失败。", style="Error.TLabel")
        self._log(message, "error")
        messagebox.showerror("扫描贴图失败", message, parent=self)

    def _on_scan_done(self, entries: list, warnings: list, directory: str) -> None:
        self._texture_entries = list(entries)
        self._fill_texture_tree(entries)
        total_bytes = sum(entry.size for entry in entries)
        self.texture_count_label.configure(
            text=f"识别到 {len(entries)} 张贴图（PNG/TIF）· 合计 {_human_size(total_bytes)}",
            style="Ok.TLabel" if entries else "Warn.TLabel",
        )
        hints = list(warnings) + services.dimension_warnings(entries)
        if hints:
            self.texture_warn_label.configure(text="⚠ " + hints[0] + (f"（另有 {len(hints) - 1} 条，见日志）" if len(hints) > 1 else ""))
            for hint in hints:
                self._log(f"⚠ {hint}", "warn")
        else:
            self.texture_warn_label.configure(text="")
        if not entries:
            self._log("该文件夹里没有找到 PNG/TIF 贴图。", "warn")
        else:
            self._log(f"贴图扫描完成：{len(entries)} 张（{directory}）。", "ok")

    def _fill_texture_tree(self, entries: list) -> None:
        for item in self.texture_tree.get_children():
            self.texture_tree.delete(item)
        for entry in entries:
            label = entry.rel if entry.rel != entry.name else entry.name
            dims = entry.dimension_label if not entry.error else "读取失败"
            self.texture_tree.insert(
                "", "end", values=(label, entry.kind_label, dims, entry.size_label)
            )

    # ------------------------------------------------------------------ 步骤导航
    def _go_back(self) -> None:
        if self._busy:
            return
        self._show_step(self._current_step - 1)

    def _go_next(self) -> None:
        if self._busy:
            return
        error = self._validate_step(self._current_step)
        if error:
            messagebox.showwarning("还不能继续", error, parent=self)
            return
        self._show_step(self._current_step + 1)

    def _validate_step(self, index: int) -> str:
        if index == 0:
            if self.mode_var.get() == "clone":
                template = self.clone_dir_var.get().strip()
                if not template:
                    return "请选择模板涂装文件夹（Community 中某个涂装的 livery 目录）。"
                if not Path(template).is_dir():
                    return f"模板涂装文件夹不存在：\n{template}"
                if not (Path(template) / "livery.cfg").is_file():
                    return (
                        "所选文件夹里没有找到 livery.cfg，它看起来不是涂装目录。\n"
                        "请选择类似 …\\liveries\\作者名\\涂装名 的文件夹。"
                    )
            else:
                if not self._profiles:
                    return "暂无内置机型，请改用「克隆现有涂装」模式（上方模式选项）。"
                if not self.profile_var.get():
                    return "请先在下拉列表里选择一个内置机型。"
            return ""
        if index == 1:
            texture_dir = self.texture_dir_var.get().strip()
            if not texture_dir:
                return "请选择贴图文件夹。"
            if not Path(texture_dir).is_dir():
                return f"贴图文件夹不存在：\n{texture_dir}"
            if not self.title_var.get().strip():
                return "请填写涂装名称（必填）。"
            return ""
        return ""

    # ------------------------------------------------------------------ 生成涂装包
    def _preflight(self, needs: list[str]) -> str:
        status = services.engine_status()
        problems = []
        for key in needs:
            item = status.get(key) or {}
            if not item.get("ready"):
                problems.append(f"· {item.get('label', key)}：{item.get('hint', '尚未就绪')}")
        if not status.get("texconv", {}).get("ready"):
            problems.append(f"· texconv：{status['texconv']['hint']}")
        return "\n".join(problems)

    def _gather_info(self) -> LiveryInfo | None:
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("缺少涂装名称", "请先填写「涂装名称」，这是必填项。", parent=self)
            return None
        return LiveryInfo(
            title=title,
            atc_id=self.atc_id_var.get().strip(),
            atc_airline=self.airline_var.get().strip(),
            icao_airline=self.icao_var.get().strip(),
            author=self.author_var.get().strip(),
            version=self.version_var.get().strip() or "1.0",
        )

    def _start_build(self) -> None:
        if self._busy:
            return
        mode = self.mode_var.get()
        needs = ["builder"] if mode != "clone" else ["clone"]
        problems = self._preflight(needs)
        if problems:
            messagebox.showerror(
                "引擎尚未就绪",
                "当前版本缺少以下组件，暂时无法生成涂装包：\n\n"
                f"{problems}\n\n"
                "你可以先在「② 贴图转换」页签把贴图转成 KTX2/DDS，等引擎更新后再打包。",
                parent=self,
            )
            self._log("生成被取消：引擎组件未就绪。", "error")
            return

        texture_dir = Path(self.texture_dir_var.get().strip())
        output_dir = self.output_dir_var.get().strip()
        if not texture_dir.is_dir():
            messagebox.showwarning("贴图文件夹无效", f"贴图文件夹不存在：\n{texture_dir}", parent=self)
            return
        if not output_dir:
            messagebox.showwarning("缺少输出目录", "请选择输出目录。", parent=self)
            return
        output_root = Path(output_dir)
        if output_root.exists() and not output_root.is_dir():
            messagebox.showwarning("输出目录无效", f"输出路径不是文件夹：\n{output_root}", parent=self)
            return
        info = self._gather_info()
        if info is None:
            return
        target_format = self.formats_var.get()
        quality_high = self.quality_var.get() == "high"
        thumbnail = self.thumbnail_var.get().strip()
        thumbnail_path = Path(thumbnail) if thumbnail else None
        if thumbnail_path is not None and not thumbnail_path.is_file():
            messagebox.showwarning("缩略图不存在", f"缩略图文件不存在：\n{thumbnail_path}", parent=self)
            return
        community_dir = self.community_var.get().strip()
        copy_to_community = bool(self.copy_community_var.get())
        if copy_to_community and not community_dir:
            messagebox.showwarning(
                "未配置 Community 路径",
                "勾选了「生成后复制到 Community」，但还没有配置 Community 路径。\n"
                "请到「③ 设置」页签自动探测或手动选择。",
                parent=self,
            )
            return

        self._set_busy(True)
        self.build_progress.configure(value=0)
        self.build_percent.configure(text="0%")
        self.result_label.configure(text="")
        self.btn_open_output.configure(state="disabled")
        self.btn_copy_community.configure(state="disabled")
        self.output_hint.configure(text="正在生成，请稍候（日志在下方实时刷新）…")
        mode_text = "内置机型" if mode != "clone" else "克隆模式"
        profile = self._profile_by_label.get(self.profile_var.get()) if mode != "clone" else None
        template = self.clone_dir_var.get().strip() if mode == "clone" else ""
        self._log("")
        self._log(f"===== 开始生成（{mode_text}）=====", "ok")
        self._log(f"涂装名称：{info.title}")
        self._log(f"输出目录：{output_root}")
        self._run_in_thread(
            self._build_worker,
            mode,
            profile,
            info,
            texture_dir,
            output_root,
            target_format,
            quality_high,
            thumbnail_path,
            copy_to_community,
            community_dir or None,
            template,
            bool(self.overwrite_var.get()),
            name="livery-builder",
        )

    def _build_worker(
        self,
        mode: str,
        profile,
        info: LiveryInfo,
        texture_dir: Path,
        output_root: Path,
        target_format: str,
        quality_high: bool,
        thumbnail_path: Path | None,
        copy_to_community: bool,
        community_dir: str | None,
        template: str,
        overwrite: bool,
    ) -> None:
        def progress(message: str, fraction: float | None) -> None:
            self._post("build_progress", message, fraction)

        try:
            if mode == "clone":
                result = services.clone_livery(
                    template_livery_dir=template,
                    info=info,
                    texture_dir=texture_dir,
                    output_root=output_root,
                    target_format=target_format,
                    quality_high=quality_high,
                    thumbnail_source=thumbnail_path,
                    overwrite=overwrite,
                    progress=progress,
                )
            else:
                result = services.build_livery(
                    profile=profile,
                    info=info,
                    texture_dir=texture_dir,
                    output_root=output_root,
                    target_format=target_format,
                    quality_high=quality_high,
                    thumbnail_source=thumbnail_path,
                    copy_to_community=copy_to_community,
                    community_dir=community_dir,
                    overwrite=overwrite,
                    progress=progress,
                )
        except LiveryForgeError as exc:
            self._post("build_error", "生成涂装包失败", str(exc), "检查贴图命名/尺寸，或先在「贴图转换」页签验证转换是否正常。")
        except Exception as exc:  # noqa: BLE001
            detail = traceback.format_exc(limit=6)
            self._post(
                "build_error",
                "生成涂装包失败（未预期错误）",
                f"{type(exc).__name__}: {exc}\n\n{detail}",
                "请把上面的日志发给开发者；可先重试一次，或换一个输出目录。",
            )
        else:
            self._post("build_done", result)

    def _on_build_progress(self, message: str, fraction: float | None) -> None:
        self._log(message)
        if fraction is not None:
            percent = max(0.0, min(1.0, float(fraction))) * 100
            self.build_progress.configure(value=percent)
            self.build_percent.configure(text=f"{percent:.0f}%")

    def _on_build_done(self, result) -> None:
        self._set_busy(False)
        self.build_progress.configure(value=100)
        self.build_percent.configure(text="100%")
        self._last_result = result
        package_dir = Path(getattr(result, "package_dir", "") or "")
        self._last_package_dir = package_dir if str(package_dir) else None
        file_count = getattr(result, "file_count", 0) or 0
        total_bytes = getattr(result, "total_bytes", 0) or 0
        self._log("")
        self._log("✅ 生成完成！", "ok")
        self._log(f"涂装包目录：{package_dir}")
        self._log(f"文件数量：{file_count} 个，总大小：{_human_size(total_bytes)}", "ok")
        for warning in getattr(result, "warnings", None) or []:
            self._log(f"⚠ {warning}", "warn")
        self.result_label.configure(
            text=f"✔ 已生成：{package_dir}（{file_count} 个文件，{_human_size(total_bytes)}）"
        )
        self.output_hint.configure(text="生成完成。可以打开输出目录，或复制到 Community。")
        self.btn_open_output.configure(state="normal" if self._last_package_dir else "disabled")
        self.btn_copy_community.configure(state="normal" if self._last_package_dir else "disabled")
        self._persist_settings(show_error=False)
        if getattr(result, "community_copied", False):
            messagebox.showinfo("完成", "涂装包已生成并复制到 Community。\n启动 MSFS 2024 即可在机库中看到。", parent=self)
        else:
            self.notebook.select(0)
            messagebox.showinfo(
                "生成完成",
                f"涂装包已生成：\n{package_dir}\n\n"
                "可以直接打开输出目录查看，或点「复制到 Community」安装到游戏。",
                parent=self,
            )

    def _on_build_error(self, title: str, message: str, suggestion: str) -> None:
        self._set_busy(False)
        self.build_progress.configure(value=0)
        self.build_percent.configure(text="0%")
        self.output_hint.configure(text="生成失败，请查看下方日志。")
        self._log("")
        self._log(f"❌ {title}", "error")
        for line in message.splitlines():
            self._log(line, "error")
        if suggestion:
            self._log(f"解决建议：{suggestion}", "warn")
        messagebox.showerror(title, f"{message}\n\n解决建议：{suggestion}", parent=self)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        try:
            self.btn_start.configure(state=state)
            self.btn_convert.configure(state=state)
            self.btn_back.configure(state="disabled" if busy or self._current_step == 0 else "normal")
            self.btn_next.configure(state="disabled" if busy or self._current_step >= 2 else "normal")
            self.btn_refresh_profiles.configure(state=state)
            self.btn_detect_community.configure(state=state)
        except tk.TclError:
            pass
        self.configure(cursor="watch" if busy else "")

    # ------------------------------------------------------------------ 输出操作
    def _open_output_dir(self) -> None:
        if self._last_package_dir is None:
            return
        if not services.open_directory(self._last_package_dir):
            messagebox.showwarning("无法打开目录", f"目录不存在或无法打开：\n{self._last_package_dir}", parent=self)

    def _copy_last_to_community(self) -> None:
        if self._last_package_dir is None:
            return
        community_dir = self.community_var.get().strip()
        if not community_dir:
            if messagebox.askyesno(
                "未配置 Community 路径",
                "还没有配置 Community 路径。要现在到「设置」页签自动探测吗？",
                parent=self,
            ):
                self.notebook.select(2)
                self._detect_community(manual=True)
            return
        if not Path(community_dir).is_dir():
            messagebox.showerror(
                "Community 路径无效",
                f"Community 目录不存在：\n{community_dir}\n请到「设置」页签重新选择。",
                parent=self,
            )
            return
        package_dir = self._last_package_dir
        self._set_busy(True)
        self._log(f"正在复制到 Community：{community_dir}", "muted")

        def worker() -> None:
            try:
                result = services.copy_package_to_community(package_dir, community_dir)
            except LiveryForgeError as exc:
                self._post("copy_error", str(exc))
            except Exception as exc:  # noqa: BLE001
                self._post("copy_error", f"{type(exc).__name__}: {exc}")
            else:
                self._post("copy_done", result)

        self._run_in_thread(worker, name="community-copy")

    def _on_copy_done(self, result: dict) -> None:
        self._set_busy(False)
        message = result.get("message", "已复制到 Community。")
        self._log(message, "ok")
        messagebox.showinfo("复制完成", message, parent=self)

    def _on_copy_error(self, message: str) -> None:
        self._set_busy(False)
        self._log(f"❌ 复制到 Community 失败：{message}", "error")
        messagebox.showerror(
            "复制失败",
            f"{message}\n\n解决建议：确认 Community 目录可写、没有被游戏占用，或手动复制输出目录。",
            parent=self,
        )

    # ------------------------------------------------------------------ 贴图转换
    def _start_convert(self) -> None:
        if self._busy:
            return
        source = self.conv_src_var.get().strip()
        output = self.conv_out_var.get().strip()
        if not source or not Path(source).is_dir():
            messagebox.showwarning("贴图文件夹无效", "请选择包含 PNG/TIF 的贴图文件夹。", parent=self)
            return
        if not output:
            messagebox.showwarning("缺少输出目录", "请选择转换结果输出目录。", parent=self)
            return
        problems = self._preflight(["texture"])
        if problems:
            messagebox.showerror(
                "贴图转换引擎尚未就绪",
                f"{problems}\n\n请确认使用的是完整版本，或稍后重试。",
                parent=self,
            )
            self._log_convert("转换被取消：引擎组件未就绪。", "error")
            return
        self._set_busy(True)
        self.conv_progress.configure(value=0)
        self.conv_percent.configure(text="0%")
        self.btn_open_convert.configure(state="disabled")
        self.conv_status.configure(text="正在转换…", style="Hint.TLabel")
        self._log_convert("")
        self._log_convert(f"===== 开始转换：{source} =====", "ok")
        self._run_in_thread(
            self._convert_worker,
            Path(source),
            Path(output),
            self.conv_format_var.get(),
            self.conv_quality_var.get() == "high",
            name="texture-converter",
        )

    def _convert_worker(self, source: Path, output: Path, target_format: str, quality_high: bool) -> None:
        def progress(message: str, fraction: float | None) -> None:
            self._post("convert_progress", message, fraction)

        try:
            result = services.convert_directory(
                source,
                output,
                target_format=target_format,
                quality_high=quality_high,
                progress=progress,
            )
        except LiveryForgeError as exc:
            self._post("convert_error", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._post(
                "convert_error",
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}",
            )
        else:
            self._post("convert_done", result)

    def _on_convert_progress(self, message: str, fraction: float | None) -> None:
        self._log_convert(message)
        if fraction is not None:
            percent = max(0.0, min(1.0, float(fraction))) * 100
            self.conv_progress.configure(value=percent)
            self.conv_percent.configure(text=f"{percent:.0f}%")

    def _on_convert_done(self, result: dict) -> None:
        self._set_busy(False)
        self.conv_progress.configure(value=100)
        self.conv_percent.configure(text="100%")
        out_dir = Path(result.get("out_dir", ""))
        self._last_convert_dir = out_dir
        self.btn_open_convert.configure(state="normal" if out_dir else "disabled")
        count = result.get("count", 0)
        elapsed = result.get("elapsed", 0.0)
        self.conv_status.configure(
            text=f"✔ 转换完成：{count} 张贴图，耗时 {elapsed:.1f} 秒。", style="Ok.TLabel"
        )
        self._log_convert("")
        self._log_convert(f"✅ 转换完成：{count} 张，耗时 {elapsed:.1f} 秒。", "ok")
        self._log_convert(f"输出目录：{out_dir}", "ok")
        for warning in result.get("warnings") or []:
            self._log_convert(f"⚠ {warning}", "warn")
        self._persist_settings(show_error=False)
        messagebox.showinfo(
            "转换完成",
            f"已转换 {count} 张贴图。\n输出目录：\n{out_dir}\n\n"
            "下一步：把这些 .KTX2 / .DDS 文件放入涂装包的 texture 文件夹。",
            parent=self,
        )

    def _on_convert_error(self, message: str) -> None:
        self._set_busy(False)
        self.conv_status.configure(text="转换失败，请查看下方日志。", style="Error.TLabel")
        self._log_convert("")
        self._log_convert(f"❌ 转换失败：{message}", "error")
        messagebox.showerror(
            "转换失败",
            f"{message}\n\n解决建议：确认目录内是 PNG/TIF 图片、尺寸为 2 的幂；可先试 samples\\textures。",
            parent=self,
        )

    def _open_convert_dir(self) -> None:
        if self._last_convert_dir is not None and services.open_directory(self._last_convert_dir):
            return
        output = self.conv_out_var.get().strip()
        if output and services.open_directory(output):
            return
        messagebox.showwarning("无法打开目录", "输出目录不存在或无法打开。", parent=self)

    # ------------------------------------------------------------------ 环境 / 探测
    def _refresh_engine_status_async(self) -> None:
        self._run_in_thread(self._engine_status_worker, name="engine-status")

    def _engine_status_worker(self) -> None:
        try:
            status = services.engine_status()
            community_dirs = services.detect_community_dirs()
        except Exception as exc:  # noqa: BLE001
            self._post("engine_status", {}, [], f"{type(exc).__name__}: {exc}")
            return
        self._post("engine_status", status, community_dirs, "")

    def _on_engine_status(self, status: dict, community_dirs: list, error: str) -> None:
        if error:
            self.status_engine.configure(text=f"环境自检失败：{error}", foreground="#b00020")
            return
        marks = {True: "√", False: "×"}
        summary = []
        for key in ("profiles", "texture", "builder", "clone", "texconv"):
            item = status.get(key) or {}
            summary.append(f"{marks.get(bool(item.get('ready')), '?')}{item.get('label', key)}")
        self.status_engine.configure(text=" ".join(summary), foreground="#333333")
        if hasattr(self, "env_labels"):
            python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            self.env_labels["runtime"].set(f"Python {python_version}（{sys.executable}）")
            for key in ("texconv", "texture", "builder", "clone", "profiles"):
                item = status.get(key) or {}
                prefix = "✔ " if item.get("ready") else "✘ "
                detail = item.get("detail") or ""
                self.env_labels[key].set(prefix + str(detail))
        # 自动填充 Community 路径
        if community_dirs and not self.community_var.get().strip():
            self.community_var.set(str(community_dirs[0]))
            self._log(f"已自动检测到 Community 目录：{community_dirs[0]}", "ok")
        if not (status.get("texconv") or {}).get("ready"):
            self._log("⚠ 未找到 texconv.exe，贴图转换/打包会失败。见「帮助」页签或 README 常见问题。", "warn")

    def _detect_community(self, manual: bool) -> None:
        def worker() -> None:
            try:
                dirs = services.detect_community_dirs()
            except Exception as exc:  # noqa: BLE001
                self._post("community_detected", [], manual, f"{type(exc).__name__}: {exc}")
                return
            self._post("community_detected", dirs, manual, "")

        self._run_in_thread(worker, name="community-detector")

    def _on_community_detected(self, dirs: list, manual: bool, error: str) -> None:
        dirs = [Path(item) for item in dirs]
        if error:
            if manual:
                messagebox.showerror("自动探测失败", f"探测 Community 目录时出错：\n{error}", parent=self)
            return
        if dirs:
            self.community_var.set(str(dirs[0]))
            if manual:
                lines = "\n".join(f"· {item}" for item in dirs)
                messagebox.showinfo("已找到 Community 目录", f"已自动填入：\n{dirs[0]}\n\n全部候选目录：\n{lines}", parent=self)
                self._log(f"已找到 Community 目录：{dirs[0]}", "ok")
        elif manual:
            messagebox.showinfo(
                "没有自动找到",
                "没有自动找到 Community 目录。\n\n"
                "解决建议：\n"
                "1. 手动选择游戏数据目录下的 Community 文件夹；\n"
                "2. 常见位置：C:\\Users\\你的用户名\\AppData\\Roaming\\Microsoft Flight Simulator 2024\\Packages 或自定义数据盘；\n"
                "3. 也可以在系统里设置环境变量 MSFS_COMMUNITY_DIR 指向它。",
                parent=self,
            )

    def _open_community_dir(self) -> None:
        target = self.community_var.get().strip()
        if not target:
            messagebox.showinfo("未设置", "还没有设置 Community 路径。", parent=self)
            return
        if not services.open_directory(target):
            messagebox.showwarning("无法打开", f"目录不存在或无法打开：\n{target}", parent=self)

    def _open_config_dir(self) -> None:
        try:
            directory = settings_dir()
        except OSError as exc:
            messagebox.showerror("无法打开配置目录", str(exc), parent=self)
            return
        if not directory.is_dir() and not services.open_directory(directory):
            messagebox.showinfo(
                "配置目录尚未创建",
                f"配置目录还没有创建：\n{directory}\n\n第一次保存设置后会自动创建。",
                parent=self,
            )
            return
        services.open_directory(directory)

    def _open_samples_dir(self) -> None:
        samples = services.open_samples_dir()
        if not services.open_directory(samples):
            messagebox.showwarning("无法打开", f"示例贴图目录不存在：\n{samples}", parent=self)

    def _open_docs_dir(self) -> None:
        docs = services.project_root() / "docs"
        if not services.open_directory(docs):
            messagebox.showwarning("无法打开", f"文档目录不存在：\n{docs}", parent=self)

    def _open_project_dir(self) -> None:
        root = services.project_root()
        if not services.open_directory(root):
            messagebox.showwarning("无法打开", f"项目目录不存在：\n{root}", parent=self)

    # ------------------------------------------------------------------ 关闭
    def _on_close(self) -> None:
        try:
            self._collect_settings().save()
        except OSError:
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _print_self_check() -> int:
    """无 GUI 的环境自检（打包脚本/CI 用）。"""
    status = services.engine_status()
    print(f"MSFS 2024 涂装工坊 v{VERSION}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    for key in ("profiles", "texture", "builder", "clone", "texconv", "pillow"):
        item = status.get(key) or {}
        print(f"  {'[OK]  ' if item.get('ready') else '[MISS]'} {item.get('label', key)}: {item.get('detail', '')}")
    communities = services.detect_community_dirs()
    print("Community:", ", ".join(str(path) for path in communities) or "未检测到")
    print("配置路径:", settings_path())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="liveryforge-gui",
        description="MSFS 2024 涂装工坊 图形界面",
    )
    parser.add_argument("--smoke", type=float, default=0.0, metavar="秒", help="启动后自动关闭（自检/打包验证用）")
    parser.add_argument("--self-check", action="store_true", help="只做环境自检并退出（不打开窗口）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)

    if args.self_check:
        return _print_self_check()

    try:
        app = LiveryForgeApp()
    except tk.TclError as exc:
        print(f"无法创建图形界面窗口：{exc}", file=sys.stderr)
        print("请在 Windows 桌面环境中运行（远程/无显示环境不支持 GUI，可改用 run_cli.bat）。", file=sys.stderr)
        return 1

    if args.smoke > 0:
        delay = max(400, int(args.smoke * 1000))
        app.after(delay, app.destroy)
        try:
            app.mainloop()
        except KeyboardInterrupt:
            return 130
        print(f"GUI 冒烟测试通过：窗口已创建并运行约 {args.smoke:g} 秒。", flush=True)
        return 0

    try:
        app.mainloop()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
