"""MSFS Livery Forge 命令行入口（T3）。

用法（在项目根目录执行）::

    python -m liveryforge list-profiles
    python -m liveryforge detect
    python -m liveryforge convert <贴图目录> -o <输出目录> --format ktx2|dds|both
    python -m liveryforge build --profile <id> --textures <目录> -o <输出目录> --title "涂装名"
    python -m liveryforge clone --template <涂装目录> --textures <目录> -o <输出目录> --title "涂装名"

约定：

* 所有帮助与错误信息为中文；
* 退出码：0 = 成功，1 = 失败，2 = 参数错误（argparse 默认），130 = 用户中断；
* 逐文件进度直接打印到控制台（每行一个文件名），结束打印摘要。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .paths import APP_DISPLAY_NAME, VERSION, default_output_dir, texconv_path
from .settings import Settings, settings_path
from .types import (
    LiveryForgeError,
    LiveryInfo,
    PackageError,
    TargetFormat,
    TextureError,
    ToolNotFoundError,
)
from .utils import human_size

PROG = "liveryforge"

_EPILOG = """\
示例（Windows，复制即可用）：
  python -m liveryforge list-profiles
  python -m liveryforge convert samples\\textures -o 输出\\贴图 --format both
  python -m liveryforge build --profile longitude --textures samples\\textures -o 输出 --title "我的座机"
  python -m liveryforge clone --template "E:\\历史\\Community\\livery-longitude-n233cl\\SimObjects\\Airplanes\\asobo_longitude\\liveries\\TimHH\\C700_N233CL" --textures samples\\textures -o 输出 --title "克隆涂装"

提示：图形界面更适合非程序员——双击 run_gui.bat 即可。
"""

_FORMAT_CHOICES = ("ktx2", "dds", "both")
_QUALITY_CHOICES = ("high", "standard")


# ---------------------------------------------------------------------------
# 输出辅助
# ---------------------------------------------------------------------------
def _configure_stdio() -> None:
    """尽量让 Windows 控制台正确显示中文（bat 里也会 chcp 65001）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _print_title(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def _make_progress_printer(prefix: str = "  "):
    """返回逐条打印进度的回调：``fn(message, fraction)``。"""

    def progress(message: str, fraction: float | None = None) -> None:
        if fraction is None:
            print(f"{prefix}· {message}", flush=True)
            return
        percent = max(0.0, min(1.0, float(fraction))) * 100.0
        # 用固定宽度，避免控制台行跳动
        print(f"{prefix}[{percent:5.1f}%] {message}", flush=True)

    return progress


def _warn(message: str) -> None:
    print(f"  ⚠ {message}", flush=True)


def _fail(message: str, suggestion: str = "") -> int:
    print()
    print(f"❌ {message}", file=sys.stderr)
    if suggestion:
        print(f"   解决建议：{suggestion}", file=sys.stderr)
    return 1


def _print_engine_status(status: dict[str, dict]) -> None:
    marks = {True: "✔", False: "✘"}
    for key in ("profiles", "texture", "builder", "clone", "texconv", "pillow"):
        item = status.get(key)
        if not item:
            continue
        mark = marks.get(bool(item.get("ready")), "?")
        detail = item.get("detail") or ""
        print(f"  {mark} {item.get('label', key)}：{detail}")
        hint = item.get("hint")
        if hint and not item.get("ready"):
            print(f"     提示：{hint}")


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def cmd_list_profiles(args: argparse.Namespace) -> int:
    from .gui import services

    profiles, error = services.load_profiles()
    _print_title("内置机型列表")
    if not profiles:
        print("  暂无内置机型。")
        if error:
            print(f"  原因：{error}")
        print("  可以先使用「克隆现有涂装」模式（clone 子命令），或使用 convert 只转换贴图。")
        return 0
    print(f"  共 {len(profiles)} 个机型 profile：")
    print()
    for index, profile in enumerate(profiles, start=1):
        print(f"  {index:>2}. {profile.name}")
        print(f"      id          : {profile.id}")
        print(f"      游戏版本    : {profile.generation}")
        print(f"      机体路径    : {profile.aircraft_path or '—'}")
        if profile.required_tags:
            print(f"      必需 tags   : {profile.required_tags}")
        if profile.notes:
            print(f"      说明        : {profile.notes}")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    from .gui import services

    status = services.engine_status()
    communities = services.detect_community_dirs()
    try:
        config_file = settings_path()
    except OSError as exc:
        config_file = Path("<无法确定>")  # type: ignore[assignment]
        print(f"  ⚠ 读取配置路径失败：{exc}")
    payload = {
        "version": VERSION,
        "settings_path": str(config_file),
        "texconv": str(texconv_path()),
        "community_dirs": [str(path) for path in communities],
        "engines": {key: {"ready": bool(value.get("ready")), "detail": value.get("detail")} for key, value in status.items()},
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    _print_title("环境探测")
    print(f"  程序版本    : {VERSION}")
    print(f"  配置文件    : {config_file}")
    print(f"  texconv     : {texconv_path()}")
    print()
    print("  引擎状态：")
    _print_engine_status(status)
    print()
    if communities:
        print("  检测到 Community 目录：")
        for path in communities:
            print(f"    · {path}")
    else:
        print("  未检测到 Community 目录。")
        print("     解决建议：在图形界面「设置」页签手动选择，或设置环境变量 MSFS_COMMUNITY_DIR。")
    return 0


def _resolve_target_format(value: str) -> TargetFormat:
    return TargetFormat(str(value).lower())


def _check_texconv() -> None:
    from .gui import services

    ok, path, hint = services.texconv_status()
    if not ok:
        raise ToolNotFoundError(hint)


def cmd_convert(args: argparse.Namespace) -> int:
    from .gui import services

    source = Path(args.textures)
    output = Path(args.output)
    _print_title(f"转换贴图：{source}")
    print(f"  输出目录    : {output}")
    print(f"  输出格式    : {args.format}")
    print(f"  质量模式    : {'高' if args.quality == 'high' else '标准'}")
    print(f"  递归扫描    : {'是' if not args.no_recursive else '否'}")
    _check_texconv()
    try:
        result = services.convert_directory(
            source,
            output,
            target_format=args.format,
            quality_high=args.quality == "high",
            progress=_make_progress_printer(),
            recursive=not args.no_recursive,
        )
    except TextureError as exc:
        return _fail(str(exc), "确认目录内有 PNG/TIF 文件；首次使用建议先试 samples\\textures。")
    except LiveryForgeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 引擎内部异常也不能出现英文堆栈给用户
        return _fail(f"转换失败：{type(exc).__name__}: {exc}", "把这段信息发给开发者；可先重试一次。")

    print()
    print("✅ 转换完成")
    print(f"  贴图数量    : {result['count']} 张")
    print(f"  耗时        : {result['elapsed']:.1f} 秒")
    print(f"  输出目录    : {result['out_dir']}")
    for warning in result.get("warnings", []):
        _warn(warning)
    print()
    print("下一步：把输出目录中的 .KTX2 / .DDS 文件放进涂装包对应 texture 文件夹。")
    return 0


def _add_livery_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--textures", required=True, metavar="<目录>", help="贴图目录（放 PNG/TIF 的文件夹）")
    parser.add_argument("-o", "--output", required=True, metavar="<输出目录>", help="生成涂装包的父目录")
    parser.add_argument("--title", required=True, metavar="<名称>", help="涂装显示名称（必填）")
    parser.add_argument("--atc-id", default="", metavar="<注册号>", help="注册号，例如 B-1234")
    parser.add_argument("--airline", default="", metavar="<航空公司>", help="航空公司名称，例如 中国东方航空")
    parser.add_argument("--icao", default="", metavar="<三字码>", help="ICAO 三字码，例如 CES")
    parser.add_argument("--author", default="", metavar="<作者>", help="作者名（默认取设置中的作者名）")
    parser.add_argument("--livery-version", default="1.0", metavar="<版本>", help="涂装版本号（默认 1.0）")
    parser.add_argument("--format", choices=_FORMAT_CHOICES, default=None, help="贴图输出格式（默认取设置，一般为 ktx2）")
    parser.add_argument("--quality", choices=_QUALITY_CHOICES, default=None, help="贴图质量（默认 high）")
    parser.add_argument("--thumbnail", default="", metavar="<图片>", help="缩略图文件（PNG/JPG）；不填则自动生成占位图")
    parser.add_argument("--copy-to-community", action="store_true", help="构建成功后复制到 Community（需配合 --community-dir 或自动探测）")
    parser.add_argument("--community-dir", default="", metavar="<目录>", help="Community 目录（默认自动探测）")
    parser.add_argument("--overwrite", action="store_true", help="覆盖输出目录中已存在的同名涂装包")


def _build_livery_info(args: argparse.Namespace, settings: Settings) -> LiveryInfo:
    title = (args.title or "").strip()
    if not title:
        raise LiveryForgeError("涂装名称（--title）不能为空。")
    author = (args.author or "").strip() or settings.author
    return LiveryInfo(
        title=title,
        atc_id=(args.atc_id or "").strip(),
        atc_airline=(args.airline or "").strip(),
        icao_airline=(args.icao or "").strip(),
        author=author,
        version=(args.livery_version or "1.0").strip() or "1.0",
    )


def _resolve_build_defaults(args: argparse.Namespace, settings: Settings) -> tuple[str, bool, bool]:
    target_format = args.format or settings.formats or "ktx2"
    quality_high = settings.quality_high if args.quality is None else (args.quality == "high")
    copy_to_community = bool(args.copy_to_community)
    return target_format, quality_high, copy_to_community


def _print_build_summary(result, title: str) -> None:
    print()
    print(f"✅ {title}完成")
    print(f"  涂装包目录  : {getattr(result, 'package_dir', '')}")
    print(f"  livery 目录 : {getattr(result, 'livery_dir', '')}")
    print(f"  manifest    : {getattr(result, 'manifest_path', '')}")
    file_count = getattr(result, "file_count", 0) or 0
    total_bytes = getattr(result, "total_bytes", 0) or 0
    print(f"  文件数量    : {file_count} 个")
    print(f"  包总大小    : {human_size(total_bytes)}")
    if getattr(result, "community_copied", False):
        print("  Community   : 已复制")
    textures = getattr(result, "textures", None) or []
    if textures:
        print(f"  转换贴图    : {len(textures)} 张")
    for warning in getattr(result, "warnings", None) or []:
        _warn(warning)
    print()
    print("下一步：启动 MSFS 2024，在机库中即可看到新涂装。")


def cmd_build(args: argparse.Namespace) -> int:
    from .gui import services

    settings = Settings.load()
    profile = services.get_profile(args.profile)
    if profile is None:
        profiles, error = services.load_profiles()
        available = "、".join(item.id for item in profiles) if profiles else "暂无"
        hint = f"可用机型 id：{available}。可先运行 liveryforge list-profiles 查看。"
        if error:
            hint += f"（读取机型失败：{error}）"
        return _fail(f"找不到机型 profile：{args.profile}", hint)

    info = _build_livery_info(args, settings)
    target_format, quality_high, copy_to_community = _resolve_build_defaults(args, settings)
    output_root = Path(args.output)
    texture_dir = Path(args.textures)
    thumbnail = Path(args.thumbnail) if args.thumbnail else None
    community = Path(args.community_dir) if args.community_dir else None

    _print_title(f"生成涂装包：{info.title}")
    print(f"  机型      : {services.format_profile_label(profile)}")
    print(f"  贴图目录  : {texture_dir}")
    print(f"  输出目录  : {output_root}")
    print(f"  输出格式  : {target_format}（质量：{'高' if quality_high else '标准'}）")
    if thumbnail:
        print(f"  缩略图    : {thumbnail}")
    _check_texconv()

    if copy_to_community:
        if community is None:
            community = services.detect_community_dir()
        if community is None:
            return _fail(
                "指定了 --copy-to-community，但未找到 Community 目录。",
                "用 --community-dir 指定，或先在图形界面「设置」里配置 Community 路径。",
            )
        print(f"  Community : {community}")

    try:
        result = services.build_livery(
            profile=profile,
            info=info,
            texture_dir=texture_dir,
            output_root=output_root,
            target_format=target_format,
            quality_high=quality_high,
            thumbnail_source=thumbnail,
            copy_to_community=copy_to_community,
            community_dir=community,
            overwrite=bool(args.overwrite),
            progress=_make_progress_printer(),
        )
    except PackageError as exc:
        return _fail(str(exc), "检查贴图命名/尺寸是否符合要求；可加 --overwrite 覆盖已经存在的输出。")
    except LiveryForgeError:
        raise
    except Exception as exc:  # noqa: BLE001
        return _fail(f"生成失败：{type(exc).__name__}: {exc}", "把这段信息发给开发者；可先用 convert 子命令验证贴图。")

    _print_build_summary(result, "涂装包生成")
    if output_root:
        settings.last_output_dir = str(output_root)
        try:
            settings.save()
        except OSError:
            pass
    return 0


def cmd_clone(args: argparse.Namespace) -> int:
    from .gui import services

    settings = Settings.load()
    info = _build_livery_info(args, settings)
    target_format, quality_high, copy_to_community = _resolve_build_defaults(args, settings)
    template = Path(args.template)
    output_root = Path(args.output)
    texture_dir = Path(args.textures)
    thumbnail = Path(args.thumbnail) if args.thumbnail else None

    _print_title(f"克隆涂装：{template.name}")
    print(f"  模板目录  : {template}")
    print(f"  新涂装名  : {info.title}")
    print(f"  贴图目录  : {texture_dir}")
    print(f"  输出目录  : {output_root}")
    print(f"  输出格式  : 按模板自动（2024 模板→KTX2 / 2020 legacy 模板→DDS）")
    print("  说明      : 克隆模式会复制模板的非纹理文件，并用你的贴图替换同名纹理。")
    _check_texconv()

    try:
        result = services.clone_livery(
            template_livery_dir=template,
            info=info,
            texture_dir=texture_dir,
            output_root=output_root,
            target_format=target_format,
            quality_high=quality_high,
            thumbnail_source=thumbnail,
            overwrite=bool(args.overwrite),
            progress=_make_progress_printer(),
        )
    except PackageError as exc:
        return _fail(str(exc), "确认 --template 指向包含 livery.cfg 的涂装文件夹；贴图目录不要为空。")
    except LiveryForgeError:
        raise
    except Exception as exc:  # noqa: BLE001
        return _fail(f"克隆失败：{type(exc).__name__}: {exc}", "把这段信息发给开发者；可先运行 detect 查看引擎状态。")

    _print_build_summary(result, "克隆涂装")
    settings.last_output_dir = str(output_root)
    try:
        settings.save()
    except OSError:
        pass
    return 0


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=f"{APP_DISPLAY_NAME} 命令行工具 —— 把 PNG 贴图变成 MSFS 2024 可用的 KTX2/DDS 涂装包。",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", metavar="<子命令>", title="可用子命令")

    # list-profiles
    p_list = subparsers.add_parser(
        "list-profiles",
        help="列出内置机型",
        description="列出程序内置的机型 profile（机型 id、中文名、适用游戏版本）。",
    )
    p_list.set_defaults(func=cmd_list_profiles)

    # detect
    p_detect = subparsers.add_parser(
        "detect",
        help="探测运行环境",
        description="探测 Community 目录、texconv、各引擎模块状态，输出中文报告。",
    )
    p_detect.add_argument("--json", action="store_true", help="以 JSON 格式输出（便于脚本调用）")
    p_detect.set_defaults(func=cmd_detect)

    # convert
    p_convert = subparsers.add_parser(
        "convert",
        help="只转换贴图（PNG/TIF -> KTX2/DDS）",
        description="把贴图目录中的 PNG/TIF 转换成 KTX2 / DDS，不生成涂装包。",
    )
    p_convert.add_argument("textures", metavar="<贴图目录>", help="包含 PNG/TIF 的文件夹")
    p_convert.add_argument("-o", "--output", required=True, metavar="<输出目录>", help="转换结果输出目录（不存在会自动创建）")
    p_convert.add_argument("--format", choices=_FORMAT_CHOICES, default="ktx2", help="输出格式：ktx2（2024）/ dds（2020 legacy）/ both（默认 ktx2）")
    p_convert.add_argument("--quality", choices=_QUALITY_CHOICES, default="high", help="质量模式：high 高 / standard 标准（默认 high）")
    p_convert.add_argument("--no-recursive", action="store_true", help="只扫描贴图目录第一层，不递归子目录")
    p_convert.set_defaults(func=cmd_convert)

    # build
    p_build = subparsers.add_parser(
        "build",
        help="用内置机型生成涂装包",
        description="按内置机型 profile 生成可直接放入 Community 的涂装包。",
    )
    p_build.add_argument("--profile", required=True, metavar="<机型 id>", help="机型 profile 的 id（用 list-profiles 查看）")
    _add_livery_options(p_build)
    p_build.set_defaults(func=cmd_build)

    # clone
    p_clone = subparsers.add_parser(
        "clone",
        help="克隆现有涂装并替换贴图",
        description="以已安装的涂装文件夹为模板，替换贴图与涂装信息后重新打包，适配任意机型。",
    )
    p_clone.add_argument("--template", required=True, metavar="<涂装目录>", help="已安装涂装的 livery 文件夹（含 livery.cfg）")
    _add_livery_options(p_clone)
    p_clone.set_defaults(func=cmd_clone)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        print()
        print("提示：不确定从哪开始？先运行 `python -m liveryforge detect`。")
        return 0
    try:
        return int(args.func(args))
    except LiveryForgeError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 1
    except Exception as exc:  # noqa: BLE001 - 兜底：不把英文堆栈直接甩给普通用户
        print()
        print(f"❌ 发生未预期的错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("   解决建议：重新运行一次；若仍失败，把这段信息发给开发者。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
