"""支持 ``python -m liveryforge ...`` 直接调用 CLI（T3）。

注意：本文件由 T3 负责（架构文档中未单列，但与 ``liveryforge/cli.py`` 同属入口层）。
如 Lead 需要把入口改为其它形式，可随时替换本文件。
"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
