"""涂装包引擎（T2）：构建、克隆、环境探测。

对外入口::

    from liveryforge.package import build_package, clone_from_template
    from liveryforge.package import detect_environment, discover_liveries
"""
from __future__ import annotations

from .builder import build_package, collect_assets, make_package_name
from .clone import clone_from_template, texture_basename
from .layout import (
    generate_layout,
    iter_package_files,
    render_layout,
    verify_layout,
    write_layout,
)
from .manifest import (
    build_manifest,
    compute_total_package_size,
    render_manifest,
    write_manifest,
)
from .simenv import (
    DiscoveryError,
    LiveryEntry,
    SimEnvironment,
    detect_environment,
    discover_all_liveries,
    discover_liveries,
    find_livery_dir,
)
from .thumbnails import generate_thumbnails

__all__ = [
    "DiscoveryError",
    "LiveryEntry",
    "SimEnvironment",
    "build_manifest",
    "build_package",
    "clone_from_template",
    "collect_assets",
    "compute_total_package_size",
    "detect_environment",
    "discover_all_liveries",
    "discover_liveries",
    "find_livery_dir",
    "generate_layout",
    "generate_thumbnails",
    "iter_package_files",
    "make_package_name",
    "render_layout",
    "render_manifest",
    "texture_basename",
    "verify_layout",
    "write_layout",
    "write_manifest",
]
