"""Tkinter 图形界面包（T3）。

模块结构：

* :mod:`liveryforge.gui.services` —— GUI/CLI 共用服务层（懒加载 T1/T2 引擎）；
* :mod:`liveryforge.gui.widgets`  —— 可复用控件（路径选择行、滚动日志、树状列表）；
* :mod:`liveryforge.gui.app`      —— 主窗口（三步流程 + 贴图转换 + 设置）。

本包 ``__init__`` 刻意保持为空，不导入 tkinter，保证 CLI 也能安全 import 本包下的服务层。
"""
