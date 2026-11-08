# 贴图指南：PNG → KTX2 / DDS

> 这份文档解释「为什么」和「怎么调」，不需要编程基础。
> 只想上手操作？看 [quickstart.md](quickstart.md)。

---

## 1. 一句话原理

游戏不能直接读 Photoshop 的 PNG。MSFS 需要两种贴图格式：

```
PNG / TIF  ──(texconv 压缩)──►  DDS (BC7/BC5/BC1/BC3 + mipmap)
                                   │
                                   ├─► 直接用（MSFS 2020 / legacy 涂装）
                                   └─► 封装成 MSFS KTX2 容器（MSFS 2024 涂装）
                                        └─ 附带 .KTX2.json 元数据
```

* **DDS**：微软 DirectX 的压缩贴图格式，2020 时代涂装用；
* **KTX2**：Khronos 的容器格式，MSFS 2024 纹理使用；Asobo 在标准容器里加了自定义
  `DFD`（像素格式描述）和 `KVD`（键值数据：`ASOBO_flags`、`ASOBO_transp` 等）；
* **`.KTX2.json` / `.DDS.json`**：同名的旁车 JSON，告诉游戏这张贴图有哪些标志位
  （是否压缩、有没有 mipmap、是否高质量、有无透明……），**必须和贴图放一起**。

工具内部调用随包的 `tools\texconv\texconv.exe`（DirectXTex）做 BC 压缩，然后自己封装
KTX2 容器和 JSON —— 全程离线。

---

## 2. 支持的贴图类型

工具根据**文件名**自动识别类型（不用你手动选）：

| 文件名包含 | 类型 | 用途 | 压缩格式 | 关键标志位 |
|---|---|---|---|---|
| `ALBD` / `ALBEDO` | 基础色 | 飞机外观颜色、图案、文字 | BC7 (vkFormat 145) | `COMPRESSION, MIPMAP[, QUALITY_HIGH]`，有 alpha 时 `HasTransp:true` |
| `COMP` / `METALROUGH` | 材质(ORM) | R=粗糙度、G=金属度、B=环境光遮蔽 | BC7 (145) | `COMPRESSION, MIPMAP, NO_GAMMA_CORRECTION, METAL_ROUGH_AO_DATA` |
| `NORM` / `NORMAL` | 法线 | 蒙皮接缝、铆钉等凹凸 | BC5 (142) | `COMPRESSION, MIPMAP, NO_GAMMA_CORRECTION` |
| `EMIS` / `LIT` | 自发光 | 灯光、屏幕、舱内照明 | BC7 (145) | `COMPRESSION, MIPMAP`（按 alpha 决定透明） |
| `DECAL` | 贴花 | 门缝、警示线、贴花层 | BC7 (145) | 同 ALBD |
| 其他名字 | 通用 | 兜底 | BC7 (145) | `COMPRESSION, MIPMAP` |

> 名字只是识别线索，不影响转换能否成功；但**保留 paintkit 的原始文件名**能保证压缩参数正确
> （例如法线必须走 BC5 才有正确的凹凸感）。

真实涂抹包中的命名示例：

```
LONGITUDE_FUSELAGE01_ALBD.PNG     → 基础色
LONGITUDE_FUSELAGE01_COMP.PNG     → 材质
A350_FUSE_PLACARDS_ALBD.TIF       → 基础色（TIF 也支持）
A320NEO_COCKPIT_DECALSALPHA_ALBD.PNG → 基础色（带 alpha 的贴花）
```

### 各种标志位是什么意思？

| JSON Flags / KVD token | 通俗解释 |
|---|---|
| `FL_BITMAP_COMPRESSION` / `COMPRESSION` | 这张贴图是 BC 压缩的（不是原始像素） |
| `FL_BITMAP_MIPMAP` / `MIPMAP` | 包含多级 mipmap（远景不闪烁、不锯齿） |
| `FL_BITMAP_QUALITY_HIGH` / `QUALITY_HIGH` | 高质量模式：彩色贴图用 BC7（更精细、文件稍大）；选“标准”时彩色贴图改用 BC1（有 alpha 用 BC3），法线始终 BC5 |
| `FL_BITMAP_NO_GAMMA_CORRECTION` | 数据型贴图（材质/法线）不做 sRGB→Linear 转换 |
| `FL_BITMAP_METAL_ROUGH_AO_DATA` | 这是 ORM 材质打包图，不是颜色图 |
| `HasTransp` / `ASOBO_transp` | 这张贴图用到透明通道 |
| `FL_BITMAP_REDUCE_LESS` | 减少过度的 mip 缩减（细节保留） |

> 这些值由工具按类型**自动生成**，正常使用不需要手动改 JSON。

---

## 3. 尺寸、mipmap 与性能

* **推荐尺寸**：`1024×1024`、`2048×2048`、`4096×4096`（2 的幂，宽高最好相同）。
* **不是 2 的幂**：工具会用 texconv 的 `-pow2` 自动缩放到最近的 2 的幂并给出警告；
  但贴图 UV 可能轻微偏移（接缝处错位），所以**正式作品请直接导出 2 的幂**。
* **mipmap**：默认自动生成完整 mip 链（`-m 0`），这是游戏远景不闪烁的关键；
  不要试图关闭。
* **体积参考**（BC7 压缩后，含 mip）：

  | 尺寸 | 单张约 |
  |---|---|
  | 1024×1024 | 1.4 MB |
  | 2048×2048 | 5.6 MB |
  | 4096×4096 | 22 MB |

  4K 贴图做 20 张就是几百 MB，注意 Community 空间和加载时间；不确定时先用 2048。

---

## 4. 透明通道（alpha）

* 需要透明的贴图（舷窗、贴花边缘、天线阴影等）：导出 PNG 时**保留 alpha，不要合并背景**；
* 全不透明的外观贴图：可以不保留 alpha，工具会按无透明处理（不会白白多一张透明通道）；
* 工具按源 PNG 是否带有效 alpha 自动写入 `HasTransp` / `ASOBO_transp`，不需要手动指定；
* 透明区域在游戏里表现为“镂空”，如果发现整块变黑/变白，先检查 alpha 是否正确。

---

## 5. 色彩空间：为什么有的贴图看起来发白

MSFS 的 KTX2 **按 PNG 的 sRGB 数值直接保存，不做 sRGB→Linear 转换**（这是从真实涂装包
逐字节验证的格式事实）。也就是说：

* 你在 Photoshop 里看到的颜色，和写进 KTX2 的数值一致；
* 但游戏渲染管线会按自己的方式解释，某些情况下机模看起来比图片更亮/更白；
* **这不是工具“转坏了”**，真实官方涂装同样如此。做涂装时以游戏内实际效果为准，
  可以在导出前把颜色稍微压暗来补偿。

材质（COMP）和法线（NORM）属于**数据贴图**，工具会写 `NO_GAMMA_CORRECTION`，避免被当成颜色处理。

---

## 6. KTX2 和 DDS 怎么选

| 对比项 | KTX2 | DDS |
|---|---|---|
| 适用游戏 | MSFS 2024（原生） | MSFS 2020 / 2024 legacy 模式、部分旧机模 |
| 文件命名 | `XXX.PNG.KTX2` + `.KTX2.json` | `XXX.PNG.DDS` + `.DDS.json`（2020 包内常见） |
| 压缩 | BC7 / BC5（+ 自定义 KTX2 容器） | BC7 / BC5 / BC1 / BC3（DX10 头） |
| 质量选项 | 高/标准（影响 `QUALITY_HIGH`） | 由 texconv 格式决定（工具按类型选择） |
| 典型机型 profile | Asobo Longitude、A380X、PMDG 777、iniBuilds A350 | FlyByWire A320neo（2020 legacy） |

不确定就选 **KTX2**；要同时维护 2020/2024 两套涂装就选 **两者都要**。

---

## 7. 命令行直接调用（批量/自动化）

```bat
:: 只转 KTX2，高质量
run_cli.bat convert D:\我的涂装\A320贴图 -o D:\输出\A320_KTX2 --format ktx2 --quality high

:: KTX2 + DDS 都输出，只扫第一层
run_cli.bat convert D:\我的涂装\A320贴图 -o D:\输出\A320_both --format both --quality high --no-recursive

:: 用内置机型打包
run_cli.bat build --profile asobo_longitude_2024 --textures D:\我的涂装\A320贴图 -o D:\输出 --title "我的 A320" --author "我的名字"
```

底层实际执行的 texconv 类似（无需手动运行，仅供理解）：

```bat
tools\texconv\texconv.exe -f BC7_UNORM -m 0 -y -o <临时目录> <输入.PNG>
tools\texconv\texconv.exe -f BC5_SNORM -m 0 -y -o <临时目录> <法线.PNG>
```

---

## 8. 常见渲染问题排查

| 游戏内现象 | 可能原因 | 处理 |
|---|---|---|
| 颜色发白/过亮 | KTX2 sRGB 数值直存特性 | 以游戏内为准，导出前压暗；见第 5 节 |
| 贴图发黑 | 透明通道/压缩格式不对；ALBD 被识别成别的类型 | 检查文件名关键字、alpha；确认是 `ALBD` |
| 法线没有凹凸感 | 法线被按 BC7 压缩（文件名没带 NORM） | 文件名保留 `NORM`/`NORMAL` 关键字 |
| 边缘出现黑/白边 | 没有保留 alpha 或 alpha 预乘问题 | 保留透明通道重新导出 PNG |
| 远景闪烁/锯齿 | 缺 mipmap | 工具默认生成；检查 `.KTX2.json` 是否被删 |
| 接缝错位/图案错位 | 尺寸不是 2 的幂被缩放 | 254/512/1024/2048/4096（2 的幂）重新导出 |
| 金属感不对/过亮 | COMP 通道打包顺序错误 | R=粗糙度、G=金属度、B=AO |
| 涂装不显示（PMDG/iniBuilds） | `required_tags` 缺失或机模未安装 | 用内置 profile 自动写入；先装好机模本体 |
| 只有部分贴图生效 | 模板里没有该贴图 / 名字不匹配 | 克隆模式下放进完整 paintkit；核对原文件名 |
| 游戏里涂装重复/旧版本 | Community 里有同名旧包 | 删除旧包后重启游戏 |

---

## 9. 贴图准备清单（打印出来对照）

- [ ] 从 paintkit 获取该机型的完整贴图（或从已安装涂装解出）；
- [ ] 只改需要改的图，**保持文件名不变**（尤其 ALBD/COMP/NORM 关键字）；
- [ ] 尺寸导出为 1024 / 2048 / 4096（2 的幂，宽高相同）；
- [ ] 需要透明的贴图保留 alpha，不合并背景；
- [ ] PNG 格式（TIF 也支持，但 PNG 兼容性最好）；
- [ ] 放到一个干净文件夹，文件名不要重复（同名会被跳过并提示）；
- [ ] 用「贴图转换」页签先试转 1～2 张，确认 KTX2/DDS 能生成；
- [ ] 再回到主流程生成完整涂装包；
- [ ] 复制到 Community → 重启游戏 → 机库验收。

---

## 10. 技术细节（给想深入的人）

MSFS KTX2 容器（已从真实文件逐字节验证）：

```
12B  标识符 AB 4B 54 58 20 32 30 BB 0D 0A 1A 0A
u32  vkFormat（145=BC7_UNORM，142=BC5_SNORM，137=BC3，133=BC1，139=BC4）
u32  pixelWidth / pixelHeight / levelCount / supercompressionScheme=0
u32  dfdByteOffset / dfdByteLength (=44；BC5/BC3/BC4 为 60) / kvdByteOffset / kvdByteLength(168/184/192/204/212，随 flags 变化)
u64  sgdByteOffset=0 / sgdByteLength=0
level index  levelCount × (u64 offset, u64 length, u64 uncompressedByteLength)
数据区  按「最小 mip → level0」顺序存放，每级 16 字节对齐
KVD      ASOBO_flags / ASOBO_opacities / ASOBO_transp / ASOBOtexversion=1 / KTXwriter=ASOBO_FlightSim
```

JSON 旁车（紧凑单行、UTF-8 无 BOM）：`{"Version":2,"SourceFileDate":<FILETIME>,"Flags":[...],"HasTransp":true}`。

DDS：DX10 头 + DXGI 格式（BC7=98、BC5_SNORM=84、BC1=71、BC3=77、BC4=80 等），mip 链完整。
这些由 `liveryforge/texture/ktx2.py`、`dds.py` 实现，测试中与真实样本逐字节比对。
