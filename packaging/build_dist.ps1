# 构建 MSFS Livery Forge 发布目录（Lead 用）
param(
    [string]$Version = "1.0.0",
    [string]$OutRoot = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # 项目根（packaging 的上一级）
if (-not $OutRoot) { $OutRoot = Join-Path $root "dist" }
$name = "MSFS-Livery-Forge-v$Version"
$dist = Join-Path $OutRoot $name
Write-Host "项目根: $root"
Write-Host "输出:   $dist"

if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# 需要随产品分发的目录/文件
$includeDirs = @("liveryforge", "runtime", "tools", "samples", "docs", "packaging")
$includeFiles = @("README.md", "ARCHITECTURE.md", "run_gui.bat", "run_cli.bat", "VERSION.txt", "THIRD-PARTY-NOTICES.md")
$excludeDirs = @("__pycache__", ".pytest_cache", "docs\research", "tools\MSFSLayoutGenerator", "backup", "tests", "reports", "dist")

foreach ($d in $includeDirs) {
    $src = Join-Path $root $d
    if (-not (Test-Path $src)) { Write-Warning "跳过缺失目录: $d"; continue }
    $dst = Join-Path $dist $d
    $exclude = @("__pycache__", ".pytest_cache", (Join-Path $src "research"), (Join-Path $src "MSFSLayoutGenerator"))
    robocopy $src $dst /E /NFL /NDL /NJH /NJS /NP /XD @exclude | Out-Null
}
foreach ($f in $includeFiles) {
    $src = Join-Path $root $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $dist $f) -Force }
}

# 清掉不应进入发布的残留
$junk = Get-ChildItem $dist -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @(".liveryforge_tmp", "__pycache__", ".pytest_cache") -or $_.Name -like ".liveryforge_*" }
foreach ($j in $junk) { Remove-Item $j.FullName -Recurse -Force -ErrorAction SilentlyContinue }

# 版本文件
"$Version" | Set-Content (Join-Path $dist "VERSION.txt") -Encoding ASCII

# 示例输出：把 Lead 生成的 2024 示例包放进 examples（若存在）
$demoDir = Join-Path $root "backup\lead\e2e_out\liveryforge-livery-b-1234"
Write-Host "demoDir=[$demoDir] exists=$(Test-Path -LiteralPath $demoDir)"
if (Test-Path -LiteralPath $demoDir) {
    $ex = Join-Path $dist "examples\Asobo-Longitude-2024-demo"
    New-Item -ItemType Directory -Force -Path $ex | Out-Null
    robocopy $demoDir $ex /E /NFL /NDL /NJH /NJS /NP | Out-Null
    Write-Host "示例包已放入 examples/Asobo-Longitude-2024-demo"
}

# 体积统计
$sum = (Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum)
Write-Host ("发布目录文件数: {0}, 总大小: {1:N1} MB" -f $sum.Count, ($sum.Sum / 1MB))

# 压缩（若已存在则覆盖）
$zip = Join-Path $OutRoot "$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $dist -DestinationPath $zip -CompressionLevel Optimal
$zipSize = (Get-Item $zip).Length / 1MB
Write-Host ("ZIP: {0} ({1:N1} MB)" -f $zip, $zipSize)
