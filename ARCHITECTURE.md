# MSFS Livery Forge — 架构与接口契约（v1）

> 本文件是团队协作的唯一架构基线。所有模块必须遵守本文件定义的接口与目录归属；
> 如需变更接口，先通知 Lead，由 Lead 更新本文件。

## 1. 产品目标

Windows 桌面工具（Tkinter GUI + CLI），让 MSFS 2024 涂装作者**只需准备 PNG/TIF 贴图**，即可：

1. 把贴图转换为 MSFS 2024 可识别的 **KTX2**（含 `.KTX2.json` 元数据和 mipmap）；
2. 把贴图转换为 MSFS 2020/2024 legacy 使用的 **DDS**（DX10 头、BC7/BC1/BC3/BC5、mipmap、`.DDS.json`）；
3. 生成**可直接放入 Community 的完整涂装包**：
   - 2024 native 模式：`manifest.json` + `layout.json` + `SimObjects/Airplanes/<aircraft>/liveries/<creator>/<livery>/`（`livery.cfg`、`texture/`、`thumbnail/`、可选 `texture.cfg`）；
   - legacy(2020) 模式：`SimObjects/Airplanes/<variant>/aircraft.cfg` + `TEXTURE.<X>/` + `thumbnail.jpg`。
4. 零配置模板：内置机型 profile；以及「克隆现有涂装」模式（选任意已安装涂装文件夹作为模板，替换贴图与配置后重新打包，适配任意机型/第三方机模）。

### 非目标（v1 不做）

- 3D 预览/在线渲染；
- 直接调用 MSFS SDK `fspackagetool`（可留作后续验证选项，但默认不依赖）；
- 自动修改/覆盖官方包文件；只生成独立 Community 包。

## 2. 运行环境与硬约束

- OS：Windows 10/11 x64；目标用户装有 MSFS 2024（不影响离线生成）。
- 运行时：**便携 Python 3.12**（`runtime/`，由打包脚本从 DSH 运行时裁剪）+ 仅依赖 **Pillow**（已含）+ 标准库。
- 压缩工具：**bundled `tools/texconv/texconv.exe`**（DirectXTex 2026.5.8.1，MIT，含 VC 运行库 DLL）。
  通过 `subprocess` 调用；不依赖系统 PATH。
- 所有面向用户的日志/错误信息使用中文；代码注释可中文。
- 禁止联网、禁止写入用户目录以外的路径（除用户明确选择的输出/Community 目录）。

## 3. 目录与文件归属（写作用域）

```
msfs-livery-forge/
  ARCHITECTURE.md              Lead
  README.md                    文档负责人 (T3)
  run_gui.bat / run_cli.bat    T3
  liveryforge/
    __init__.py                Lead
    types.py                   Lead（共享 dataclass，冻结）
    paths.py                   Lead（工具/资源路径解析）
    utils.py                   Lead（通用小工具：日志、文件、校验）
    texture/                   T1
      __init__.py
      classify.py              文件名 -> TextureKind / slot 推断
      texconv.py               texconv 定位、调用、缓存、错误处理
      dds.py                   DDS(DX10) 读写：解析/生成、mip 链尺寸
      ktx2.py                  MSFS KTX2 容器读写（DFD/KVD/level index）
      pipeline.py              编排：PNG/TIF -> DDS/KTX2 + json sidecar
    package/                   T2
      __init__.py
      simenv.py                Community/Official 路径探测、涂装发现、基包探测
      layout.py                layout.json 生成/校验（FILETIME/size/hash 规则）
      manifest.py              manifest.json 生成
      liverycfg.py             livery.cfg / aircraft.cfg / texture.cfg 生成
      thumbnails.py            缩略图生成（尺寸/命名按 2024 规范）
      builder.py               2024 native 包构建
      clone.py                 克隆现有涂装模式
    profiles/                  T2
      __init__.py              registry：list_profiles()/get_profile()
      data/*.json              机型 profile
    gui/                       T3
      __init__.py
      app.py                   Tkinter 主窗口
      widgets.py               可复用控件（目录选择、拖放提示、日志面板）
    cli.py                     T3
    settings.py                T3（JSON 配置持久化：%APPDATA%/MSFSLiveryForge/settings.json）
  tools/texconv/               Lead 已放置（勿改）
  tools/MSFSLayoutGenerator/   Lead 已放置（仅验证用，不随产品调用）
  samples/                     T3（示例 PNG、示例输入说明）
  tests/                       各自负责对应测试；验证脚本 tests/verify/ 归 QA
  docs/                        T3（quickstart、FAQ、KTX2/DDS 说明）
  docs/research/               Lead（真实包结构/格式研究资料，只读）
  packaging/                   T3（便携运行时打包脚本）
```

**规则**：只修改自己作用域内的文件；需要改共享文件时先在团队任务/消息中说明，由 Lead 修改。
新增文件放在自己作用域内。

## 4. 共享契约（`liveryforge/types.py`，已冻结）

```python
class TextureKind(str, Enum): ALBEDO/COMP/NORMAL/DECAL/EMISSIVE/GENERIC
class TargetFormat(str, Enum): KTX2/DDS/BOTH

@dataclass
class TextureAsset:
    source: Path              # 输入文件
    name: str                 # 原始文件名（含扩展名），如 "A320_FUSE_ALBD.PNG"
    kind: TextureKind

@dataclass
class ConvertOptions:
    targets: TargetFormat = TargetFormat.KTX2
    quality_high: bool = True      # 对应 FL_BITMAP_QUALITY_HIGH / KVD QUALITY_HIGH
    mipmaps: bool = True
    force_no_alpha: bool = False
    texconv: Path | None = None    # None=自动定位
    workers: int = 4
    timeout: int = 300

@dataclass
class ConvertedTexture:
    asset: TextureAsset
    outputs: dict[str, Path]   # key: "ktx2" | "dds" | "ktx2_json" | "dds_json"
    width: int
    height: int
    mip_levels: int
    elapsed: float = 0.0

# progress callback: fn(message: str, fraction: float | None) -> None

@dataclass
class LiveryInfo:
    title: str                 # 涂装显示名（必填）
    atc_id: str = ""           # 注册号
    atc_airline: str = ""      # 航空公司名
    icao_airline: str = ""     # 三字码
    ui_variation: str = ""     # 默认=title
    author: str = ""           # creator
    version: str = "1.0"
    description: str = ""

@dataclass
class Profile:
    id: str
    name: str                    # 中文显示名
    generation: str              # "2024" | "2020"
    aircraft_path: str           # 相对包根，如 "SimObjects/Airplanes/asobo_longitude"
    creator_folder: str          # liveries 下第一层目录，默认 "liveryforge"
    content_type: str = "LIVERY" # LIVERY | AIRCRAFT
    package_order_hint: str = "SIMOBJECTS_PATCH"
    required_tags: str = ""      # livery.cfg [Selection] required_tags
    texture_cfg_fallbacks: list[str] = field(default_factory=list)  # 相对 texture/ 的 fallback，如 "..\\..\\..\\..\\texture"
    thumbnail_sizes: dict[str, int] = field(default_factory=dict)   # 文件名 -> 长边像素
    base_container: str = ""     # 2020 模式 aircraft.cfg base_container
    template_livery_dir: str = ""# 可选：包内模板（不常用）
    notes: str = ""              # 给用户看的说明/前置条件

@dataclass
class BuildRequest:
    profile: Profile
    info: LiveryInfo
    texture_dir: Path
    output_root: Path
    formats: TargetFormat = TargetFormat.KTX2
    quality_high: bool = True
    thumbnail_source: Path | None = None   # 用户截图；None 则生成占位图
    include_json_sidecars: bool = True
    copy_to_community: bool = False
    community_dir: Path | None = None
    overwrite: bool = False

@dataclass
class BuildResult:
    package_dir: Path
    livery_dir: Path
    manifest_path: Path
    layout_path: Path
    textures: list[ConvertedTexture]
    warnings: list[str]
    log: list[str]
    file_count: int
    total_bytes: int
```

## 5. 纹理引擎契约（T1）

- 入口：
  ```python
  # liveryforge/texture/pipeline.py
  def convert_textures(assets: list[TextureAsset],
                       out_dir: Path,
                       options: ConvertOptions,
                       progress: Callable[[str, float | None], None] | None = None)
      -> list[ConvertedTexture]
  ```
  - 输出文件命名：
    - KTX2：`<原始文件名>.KTX2`（如 `A320_FUSE_ALBD.PNG` → `A320_FUSE_ALBD.PNG.KTX2`）
    - KTX2 元数据：`<原始文件名>.KTX2.json`
    - DDS：`<原始文件名>.DDS`（2020 包实际为 `<原名>.PNG.DDS`；由 package 层负责最终命名，纹理层默认 `<原名>.DDS`）
    - DDS 元数据：`<...>.DDS.json`
  - 幂等：可重复转换，覆盖旧文件。
  - 失败必须抛出带文件名的 `TextureError`，且不留下半成品文件。

### 5.1 texconv 调用（已实测可用）

```bat
tools\texconv\texconv.exe -f BC7_UNORM  -m 0 -y -o <tmp_out> <input.png>
tools\texconv\texconv.exe -f BC1_UNORM  -m 0 -y -o <tmp_out> <input.png>
tools\texconv\texconv.exe -f BC3_UNORM  -m 0 -y -o <tmp_out> <input.png>
tools\texconv\texconv.exe -f BC5_SNORM  -m 0 -y -o <tmp_out> <input.png>
```
- `-m 0` 自动生成完整 mip 链；输出 DX10 DDS（BC7 → DXGI_FORMAT 98）。
- texconv 要求输出目录已存在；输出文件名小写 `.dds`，需要重命名。
- 建议 `-sepalpha` 仅用于需要保留 alpha 覆盖率的 albedo/decal mip；默认不加（与真实包一致）。
- 输入支持 PNG/TIF（WIC）。

### 5.2 纹理类型 → 压缩格式 / JSON Flags / KVD flags 映射（已从真实包验证）

| kind | vkFormat | DFD | JSON Flags | KVD ASOBO_flags | HasTransp |
|---|---|---|---|---|---|
| ALBEDO | 145 BC7_UNORM | BC7 DFD | `FL_BITMAP_COMPRESSION, FL_BITMAP_MIPMAP[, FL_BITMAP_QUALITY_HIGH]` | BILINEAR, COMPRESSION, MIPMAP, REDUCE_LESS, PLATFORM_FORMAT, QUALITY_HIGH | 透明图时 `"HasTransp":true` |
| DECAL | 145 BC7_UNORM | 同上 | 同 ALBEDO | 同 ALBEDO | 同上 |
| COMP | 145 BC7_UNORM | 同上 | + `FL_BITMAP_NO_GAMMA_CORRECTION, FL_BITMAP_METAL_ROUGH_AO_DATA` | BILINEAR, COMPRESSION, MIPMAP, PLATFORM_FORMAT, QUALITY_HIGH(, NO_GAMMA_CORRECTION, METAL_ROUGH_AO_DATA) | 无 |
| NORMAL | 142 BC5_SNORM | BC5 DFD | 待 T1 从真实 NORM 侧车确认（预期 COMPRESSION, MIPMAP, NO_GAMMA_CORRECTION） | 同上(待确认) | 无 |
| EMISSIVE/MASK/其它 | 145 BC7_UNORM | BC7 DFD | 至少 COMPRESSION, MIPMAP | 至少 COMPRESSION, MIPMAP | 按 alpha |

- 低质量模式：JSON 去掉 `FL_BITMAP_QUALITY_HIGH`；KVD flags 去掉 `QUALITY_HIGH`（其余不变）。
- `force_no_alpha=True`：JSON 不写 `HasTransp`；`ASOBO_transp` 值 0；alpha 在转换前丢弃。
- **色彩空间**：ALBEDO/DECAL 直接按 PNG 的 sRGB 数值编码，**不做** sRGB→Linear 转换（真实 KTX2 打开时发白即此原因）；COMP/NORMAL 数据按原值编码，并置 `NO_GAMMA_CORRECTION`。

### 5.3 MSFS KTX2 容器（权威格式，已用真实文件逐字节验证）

标准 Khronos KTX2 容器 + Asobo 自定义 DFD/KVD：

```
0   12B  identifier: AB 4B 54 58 20 32 30 BB 0D 0A 1A 0A
12  u32  vkFormat            (145=BC7_UNORM, 142=BC5_SNORM, 137=BC3_UNORM, 133=BC1_RGBA_UNORM, 139=BC4_UNORM)
16  u32  typeSize = 1
20  u32  pixelWidth
24  u32  pixelHeight
28  u32  pixelDepth = 0
32  u32  layerCount = 0
36  u32  faceCount = 1
40  u32  levelCount
44  u32  supercompressionScheme = 0
48  u32  dfdByteOffset
52  u32  dfdByteLength          (恒 44)
56  u32  kvdByteOffset
60  u32  kvdByteLength          (184 或 212)
64  u64  sgdByteOffset = 0
72  u64  sgdByteLength = 0
80  level index: levelCount × (u64 offset, u64 length, u64 uncompressedByteLength)
    - 每个 mip 的 offset 是文件内绝对偏移；数据按 **最小 mip → level0 最大** 顺序连续存放
    - 起始数据位置 = align16(80 + 24*levelCount + 44 + kvdLen)
    - BC7/BC1/BC3/BC5 mip 尺寸 = 块数(ceil(w/4)*ceil(h/4))*16 字节（最小 16）
dfdByteOffset 起 44B: 见 docs/research/ktx2_dfd_samples.json（各 vkFormat 的 DFD 逐字节模板，直接复制）
kvdByteOffset 起: TLV 序列（每项：u32 keyAndValueLen，key UTF-8+NUL，剩余字节为 value）
  已实测条目（顺序固定）：
   1) "ASOBO_flags\0" + value = "BILINEAR\0COMPRESSION\0MIPMAP\0[REDUCE_LESS\0]PLATFORM_FORMAT\0[QUALITY_HIGH\0][NO_GAMMA_CORRECTION\0][METAL_ROUGH_AO_DATA\0]"
   2) "ASOBO_opacities\0"（value 空）
   3) "ASOBO_transp\0" + 1 字节 value (0/1)
   4) "ASOBOtexversion\0" + u32 LE = 1
   5) "KTXwriter\0" + "ASOBO_FlightSim\0"
  每个 entry 补 0 到 4 字节对齐；整个 KVD 末尾不再额外补到 8 字节（实测 168/184/192/212，均为 4 的倍数；多补会导致 KTX2 不兼容）。
```

JSON sidecar（`.KTX2.json`，UTF-8 无 BOM，紧凑单行）：
```json
{"Version":2,"SourceFileDate":<Win32 FILETIME 100ns>,"Flags":[...],"HasTransp":true}
```
- `SourceFileDate` = 源 PNG 的最后修改时间转 Windows FILETIME（1601 epoch，100ns）。
- 字段顺序与真实文件一致：Version, SourceFileDate, Flags, [HasTransp]。
- 结尾无换行（真实文件无换行）。T1 需按真实文件字节对比验证。

### 5.4 验收标准（T1）

1. 用真实 KTX2 文件（如 `docs/research/sample.ktx2`）的 payload + 元数据重建，**输出与原始文件逐字节一致**（round-trip 测试）。
2. 给定生成的 BC7 DDS（texconv 输出）能正确解析为 mip 链并封装成结构自洽的 KTX2；用自研 parser 校验 offset/长度/对齐。
3. 对 `samples/textures/*.PNG` 生成 KTX2 + JSON，JSON 与真实包同类型文件字段集合一致。
4. DDS 输出 DX10 头、DXGI 98/BC7（或按类型），mip 数正确；与真实 2020 包 DDS 头一致。
5. 单元测试 `tests/test_texture_*.py` 全部通过；转换 20 个 4K 贴图 ≤ 60s（RTX4060 级别，可并行）。

## 6. 涂装包引擎契约（T2）

- 入口：
  ```python
  def build_package(req: BuildRequest,
                    progress: Callable[[str, float | None], None] | None = None) -> BuildResult
  def clone_from_template(template_livery_dir: Path, req: BuildRequest,
                          progress: ... ) -> BuildResult
  ```
- 2024 native 包结构（必须与真实包一致）：
  ```
  <output_root>/<package_name>/
    manifest.json
    layout.json
    SimObjects/Airplanes/<aircraft_path>/liveries/<creator>/<livery_name>/
      livery.cfg
      texture/ <*.PNG.KTX2> <*.PNG.KTX2.json> [texture.cfg]
      thumbnail/ thumbnail.png thumbnail_small.png [thumbnail_side.png thumbnail_button.png thumbnail_popup.png]
  ```
  - `livery.cfg` 用 CRLF、UTF-8（无 BOM）；节名/键名大小写按真实样本（`[Version] major/minor`、`[GENERAL] name/atc_id/atc_airline/icao_airline/ui_variation`、`[Selection] required_tags`）。
  - 真实样本：`docs/research/livery_structures.txt`（Longitude/A380/A350/legacy 四种）。
  - manifest.json：`content_type` 为 `LIVERY`；`builder`=`Microsoft Flight Simulator 2024`；`export_type`=`Community`；`package_order_hint` 按样本；`total_package_size` 为**20 位零填充十进制字符串**，值 = 包内所有文件字节数之和（**包含 layout.json 与 manifest.json 自身**）。实测 Longitude=597290953 → `"00000000000597290953"`；manifest 自身为 2 空格缩进、CRLF、无 BOM、无尾换行；字段顺序按真实样本。
  - layout.json：`{"content":[{"path":..., "size":..., "date":<FILETIME of file>}]}`；path 用 `/`；**不包含 manifest.json，也不包含 layout.json 自身**；按 path 的 casefold 排序；2 空格缩进、LF、无 BOM、无尾换行。用 `tools/MSFSLayoutGenerator/MSFSLayoutGenerator.exe` 交叉验证（它写出的 date = 文件 mtime 的 FILETIME，100ns 精度）。
  - livery.cfg：CRLF、UTF-8 无 BOM、无尾换行；节名/键名/字段集合按机型真实样本（Longitude 仅 `[Version]`+`[GENERAL] Name`；A380 `[version]`+`[GENERAL] Name`+`[FLTSIM]`；PMDG `[Version]`+`[GENERAL]`+`[Selection] required_tags`）。
- 2020/legacy 包结构：`aircraft.cfg`（[VERSION]、[VARIATION] base_container、[FLTSIM.0] title/texture/ui_variation/atc_id/...）+ `TEXTURE.<X>/`（`*.PNG.DDS` + `.DDS.json` + texture.CFG + thumbnail.jpg/thumbnail_small.jpg）。
- 缩略图：默认从用户提供的截图生成；否则生成品牌占位图（Pillow，2000×? 按真实尺寸，见 docs/research）。尺寸/命名必须能通过样例对比。
- **克隆模式**：
  - 输入：已安装涂装 livery 文件夹（含 livery.cfg），新贴图目录，LiveryInfo，输出目录。
  - 复制模板全部非纹理文件与缺失纹理；把贴图中同名基名（忽略大小写、去掉 `.PNG`/`.TIF` 扩展层次）的 KTX2 替换为新转换结果；更新 livery.cfg 的 name/atc_id/atc_airline/icao_airline；重新生成 manifest/layout/thumbnail。
  - 必须报告哪些模板纹理被替换、哪些保留。
- 机型 profile 至少 3 个（真实结构验证）：Asobo Longitude（2024）、FlyByWire A380X（2024）、PMDG 777-300ER（2024, required_tags）；另加 FBW A320neo legacy(DDS) 1 个。每个 profile 附 `notes` 说明依赖的机模。
- 测试：用真实包重算 layout/manifest 对比；构建样包并校验文件完整性。

## 7. GUI / CLI / 文档（T3）

- CLI 命令（argparse，全部中文帮助）：
  ```
  python -m liveryforge list-profiles
  python -m liveryforge detect
  python -m liveryforge convert <贴图目录> -o <输出目录> --format ktx2|dds|both [--quality high|standard]
  python -m liveryforge build --profile <id> --textures <目录> -o <输出目录> --title "..." [--atc-id ...] [--author ...]
  python -m liveryforge clone --template <涂装目录> --textures <目录> -o <输出目录> --title "..."
  ```
- GUI（Tkinter，中文，简单 3 步）：
  1. 选择机型（下拉：内置 profile / 克隆现有涂装）；
  2. 选择贴图文件夹（显示识别到的贴图列表与类型）、填写涂装信息（标题/注册号/航司/作者/版本）；
  3. 输出目录 → 「开始生成」；进度条 + 日志 + 完成后「打开输出文件夹 / 复制到 Community」。
  另有「贴图转换」独立页签（仅 PNG→DDS/KTX2）。
- 设置页：Community 路径（自动探测 + 手动选择）、texconv 路径（默认内置）、语言（v1 仅中文）。
- `run_gui.bat` / `run_cli.bat`：优先 `runtime\python.exe`，否则回退系统 python，给出中文提示。
- 便携运行时脚本 `packaging/build_runtime.ps1`：从 DSH python 目录裁剪出 `runtime/`（python.exe、DLLs、Lib 标准库、tcl/tk、Pillow），生成后必须实际启动 GUI 验证。
- 文档：`README.md`（5 分钟上手，含「作者只准备贴图」的两条路径）、`docs/quickstart.md`、`docs/texture-guide.md`（KTX2 vs DDS、命名规则、常见问题）。

## 8. 研究与素材

- 真实安装路径（本机）：
  - MSFS 2024：`E:\SteamLibrary\steamapps\common\MSFS2024`（Steam）
  - 数据根：`E:\历史`（UserCfg.opt `InstalledPackagesPath "E:\历史"`）；Community=`E:\历史\Community`，Community2024=`E:\历史\Community2024`
  - SDK：`C:\MSFS 2024 SDK`（版本 1.7.3）
  - 涂装样本：`E:\历史\Community\livery-longitude-n233cl`、`KFS_FBWA380_ANA-JA382A_2024`、`jeffs-livery-ini-a350-900-CAL-B18901`、`flybywire-aircraft-a320-neo-livery-CHH`
- `docs/research/` 内为脱敏后的格式研究资料（结构 dump、DFD 模板、解析脚本），**只读参考，不随产品打包**。

## 9. 里程碑与验收

1. **M1 纹理**：KTX2 round-trip 字节一致；DDS 输出正确；samples 转换通过。
2. **M2 涂装包**：至少 3 个 2024 profile + 1 legacy 样包生成，结构/清单与真实包对齐。
3. **M3 GUI**：从空目录开始，按「选机型→选贴图→生成」3 步产出可复制到 Community 的包；便携运行时双击可用。
4. **QA 独立验证**：不读实现代码，按用户流程验收；检查包结构、KTX2 头部、JSON、layout/manifest、缩略图；出具报告。
