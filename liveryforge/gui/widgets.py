"""GUI 可复用控件（T3）：路径选择行、滚动日志、标签+单选框组等。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Iterable, Sequence


class PathRow(ttk.Frame):
    """一行「标签 + 输入框 + 浏览按钮」。

    ``kind`` 取值 ``"dir"`` / ``"file"`` / ``"open"``；选择后写回 ``StringVar``。
    """

    def __init__(
        self,
        master: tk.Misc,
        label: str,
        variable: tk.StringVar,
        kind: str = "dir",
        *,
        filetypes: Sequence[tuple[str, str]] | None = None,
        on_change: Callable[[str], None] | None = None,
        label_width: int = 12,
    ) -> None:
        super().__init__(master)
        self.variable = variable
        self.kind = kind
        self.filetypes = filetypes
        self.on_change = on_change
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text=label, width=label_width, anchor="w").grid(row=0, column=0, sticky="w")
        self.entry = ttk.Entry(self, textvariable=variable)
        self.entry.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.button = ttk.Button(self, text="浏览…", width=8, command=self._browse)
        self.button.grid(row=0, column=2, sticky="e")

    def _browse(self) -> None:
        current = self.variable.get().strip()
        initial = current if current and Path(current).exists() else str(Path.home())
        selected = ""
        if self.kind == "dir":
            selected = filedialog.askdirectory(parent=self, initialdir=initial, title="选择文件夹")
        elif self.kind == "open":
            selected = filedialog.askopenfilename(parent=self, initialdir=initial, title="选择图片")
        else:
            selected = filedialog.askopenfilename(
                parent=self,
                initialdir=initial,
                title="选择文件",
                filetypes=self.filetypes or [("所有文件", "*.*")],
            )
        if selected:
            self.variable.set(selected)
            if self.on_change is not None:
                self.on_change(selected)


class ScrolledLog(ttk.Frame):
    """只读的滚动日志面板（自动滚动到底部）。"""

    def __init__(self, master: tk.Misc, height: int = 12) -> None:
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.text = tk.Text(
            self,
            height=height,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            background="#1e1e1e",
            foreground="#dcdcdc",
            insertbackground="#dcdcdc",
            relief="flat",
            padx=8,
            pady=6,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.tag_configure("warn", foreground="#f0c060")
        self.text.tag_configure("error", foreground="#ff7b72")
        self.text.tag_configure("ok", foreground="#7ee787")
        self.text.tag_configure("muted", foreground="#9aa0a6")

    def append(self, message: str, tag: str | None = None) -> None:
        self.text.configure(state="normal")
        if tag:
            self.text.insert("end", message + "\n", tag)
        else:
            self.text.insert("end", message + "\n")
        self.text.configure(state="disabled")
        self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class RadioRow(ttk.Frame):
    """「标签 + 一组单选框」，每项 ``(显示文字, 值)``。"""

    def __init__(
        self,
        master: tk.Misc,
        label: str,
        variable: tk.StringVar,
        options: Iterable[tuple[str, str]],
        *,
        label_width: int = 12,
        command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        ttk.Label(self, text=label, width=label_width, anchor="w").grid(row=0, column=0, sticky="w")
        frame = ttk.Frame(self)
        frame.grid(row=0, column=1, sticky="w")
        self._buttons: list[ttk.Radiobutton] = []
        for index, (text, value) in enumerate(options):
            button = ttk.Radiobutton(
                frame, text=text, value=value, variable=variable, command=command
            )
            button.pack(side="left", padx=(0, 14) if index < 10 else (0, 0))
            self._buttons.append(button)


class ScrollableFrame(ttk.Frame):
    """带垂直滚动条的容器（小屏幕上内容放不下时可滚动）。

    用 ``inner`` 属性拿到真正放内容的 Frame；构建完内容后调用
    ``bind_mousewheel()`` 让鼠标滚轮在整个内容区生效。
    """

    def __init__(self, master: tk.Misc, *, padding=(0, 0)) -> None:
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        try:
            background = self.winfo_toplevel().cget("background")
        except tk.TclError:
            background = "#f0f0f0"
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=background)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.inner = ttk.Frame(self.canvas, padding=padding)
        self.inner.columnconfigure(0, weight=1)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_inner_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.after_idle(self._refresh_scrollbar)

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self.after_idle(self._refresh_scrollbar)

    def _refresh_scrollbar(self) -> None:
        """内容放得下时隐藏滚动条，避免每页都出现无用滚动条。"""
        try:
            content_height = self.inner.winfo_reqheight()
            view_height = self.canvas.winfo_height()
        except tk.TclError:
            return
        needed = content_height > view_height + 4
        try:
            visible = bool(self.scrollbar.winfo_ismapped())
            if needed and not visible:
                self.scrollbar.grid()
            elif not needed and visible:
                self.scrollbar.grid_remove()
        except tk.TclError:
            pass

    def _on_mousewheel(self, event) -> None:
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return  # 内容没超出，不需要滚动
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def bind_mousewheel(self) -> None:
        """给内容区所有子控件绑定滚轮（构建完内容后调用一次）。"""

        def walk(widget: tk.Misc) -> None:
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            for child in widget.winfo_children():
                walk(child)

        walk(self.inner)
        walk(self.canvas)


class LabeledValue(ttk.Frame):
    """只读的「标签：值」显示行（用于环境状态、配置路径等）。"""

    def __init__(self, master: tk.Misc, label: str, value: str = "", *, label_width: int = 12) -> None:
        super().__init__(master)
        self.variable = tk.StringVar(value=value)
        ttk.Label(self, text=label, width=label_width, anchor="w").grid(row=0, column=0, sticky="w")
        ttk.Label(self, textvariable=self.variable, anchor="w", foreground="#444").grid(
            row=0, column=1, sticky="w"
        )

    def set(self, value: str) -> None:
        self.variable.set(value)
