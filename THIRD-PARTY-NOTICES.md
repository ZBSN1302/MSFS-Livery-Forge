# 第三方声明 / Third-Party Notices

MSFS Livery Forge（MSFS 2024 涂装工坊）随包分发以下第三方组件。它们各自保留原始许可证，
本项目仅以未修改的二进制或运行时形式随包分发。

## 1. DirectXTex / texconv.exe

- 组件：`tools/texconv/texconv.exe` 及其依赖的 Microsoft Visual C++ 运行库 DLL
- 版权：Copyright (C) Microsoft Corporation
- 许可证：MIT License
- 用途：把 PNG/TIF 压缩为 BC7/BC1/BC3/BC5（DDS），项目再用纯 Python 封装为 MSFS KTX2
- 许可证全文见：`tools/texconv/LICENSE`
- 项目主页：https://github.com/microsoft/DirectXTex

## 2. Python 3.12 便携运行时

- 组件：`runtime/`（python.exe、python312.dll、标准库、Tcl/Tk）
- 版权：Copyright (C) Python Software Foundation
- 许可证：PSF License Agreement
- 许可证全文见：`runtime/LICENSE.txt`

## 3. Pillow

- 组件：`runtime/Lib/site-packages/PIL`（随便携运行时分发）
- 版权：Copyright (C) 2010-2026 Jeffrey A. Clark 与贡献者
- 许可证：MIT-CMU / HPND License
- 项目主页：https://python-pillow.org

## 4. Tcl/Tk

- 组件：`runtime/tcl/`、`runtime/Lib/tkinter`（图形界面）
- 版权：Tcl/Tk 作者与 Scriptics/Sun 等
- 许可证：BSD 风格（见 `runtime/tcl/tcl8.6/license.terms`、`runtime/tcl/tk8.6/license.terms`）

## 5. MSFSLayoutGenerator.exe（仅开发期校验，产品流程不调用）

- 组件：`tools/MSFSLayoutGenerator/MSFSLayoutGenerator.exe`
- 版权：Copyright (c) 2020 Brandon Filer
- 许可证：MIT License（见 `tools/MSFSLayoutGenerator/LICENSE`）
- 项目主页：https://github.com/HughesMDflyer4/MSFSLayoutGenerator

## 关于 MSFS KTX2 格式

本项目的 KTX2 封装器为独立实现：通过分析作者本机已安装的第三方涂装文件，
还原了 MSFS 2024 使用的 Khronos KTX2 容器结构、Asobo 自定义 DFD/KVD 元数据与 JSON sidecar 规则。
项目中不包含 Microsoft/Asobo 的代码或二进制。

## 商标

《Microsoft Flight Simulator》及其相关商标归 Microsoft Corporation 所有。
本项目为第三方工具，与 Microsoft / Asobo Studio 无隶属关系。
生成的涂装版权归其作者；发布前请遵守对应机模与涂装作者的许可条款。
