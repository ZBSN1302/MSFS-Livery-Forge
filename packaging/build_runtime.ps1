<#
.SYNOPSIS
    从本机 DSH 的 Python 3.12 目录裁剪出项目自带的便携运行时 runtime\。

.DESCRIPTION
    目标：用户不需要安装 Python，双击 run_gui.bat 即可使用。

    复制内容：
      python.exe / pythonw.exe / python3.dll / python312.dll / vcruntime140*.dll
      DLLs\（排除 _test*.pyd 等测试用扩展）
      Lib\（排除 site-packages、test、idlelib、ensurepip、lib2to3、turtledemo、__pycache__）
      Lib\site-packages\PIL（Pillow，含 _imaging 等 .pyd）
      tcl\tcl8.6、tcl\tk8.6（Tkinter 运行时需要的 Tcl/Tk 脚本）

    验证：
      1. runtime\python.exe -c "import tkinter, PIL; print('OK')"
      2. runtime\python.exe -m liveryforge.gui.app --smoke N（真实创建窗口并运行 N 秒）
      3. Start-Process 启动 GUI，运行 N 秒后结束，确认无异常退出

    最后在 packaging\runtime_report.txt 输出体积报告。

.PARAMETER Source
    DSH Python 目录。默认 C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python

.PARAMETER Destination
    输出目录，默认 <项目根>\runtime

.PARAMETER Force
    目标已存在时先删除再重建。

.PARAMETER SkipVerify
    跳过所有验证（只复制）。

.PARAMETER GuiSeconds
    GUI 实际运行验证的秒数，默认 5。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_runtime.ps1 -Force
#>
[CmdletBinding()]
param(
    [string]$Source = "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python",
    [string]$Destination = "",
    [switch]$Force,
    [switch]$SkipVerify,
    [int]$GuiSeconds = 5
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Destination) { $Destination = Join-Path $ProjectRoot "runtime" }

$ReportPath = Join-Path $PSScriptRoot "runtime_report.txt"
$RuntimePython = Join-Path $Destination "python.exe"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Copy-Tree {
    param(
        [string]$SourceDir,
        [string]$TargetDir,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )
    if (-not (Test-Path -LiteralPath $SourceDir)) { return }
    $null = New-Item -ItemType Directory -Force -Path $TargetDir
    Get-ChildItem -LiteralPath $SourceDir -Force | ForEach-Object {
        if ($_.PSIsContainer) {
            if ($ExcludeDirs -contains $_.Name) { return }
            Copy-Tree -SourceDir $_.FullName -TargetDir (Join-Path $TargetDir $_.Name) -ExcludeDirs $ExcludeDirs -ExcludeFiles $ExcludeFiles
        }
        else {
            if ($ExcludeFiles -contains $_.Name) { return }
            if ($_.Extension -eq ".pyc") { return }
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $TargetDir $_.Name) -Force
        }
    }
}

function Invoke-Native {
    param([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory)
    Push-Location -LiteralPath $WorkingDirectory
    try {
        $output = & $FilePath @Arguments 2>&1 | Out-String
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    return [pscustomobject]@{ ExitCode = $code; Output = $output }
}

# ---------------------------------------------------------------------------
# 0. 检查源目录
# ---------------------------------------------------------------------------
Write-Step "检查源 Python 目录"
if (-not (Test-Path -LiteralPath (Join-Path $Source "python.exe"))) {
    throw "找不到源 Python：$Source\python.exe。请确认 DSH 运行时路径，或用 -Source 指定。"
}
$SourceVersion = & (Join-Path $Source "python.exe") -c "import sys; print(sys.version.split()[0])"
Write-Host "源目录  : $Source"
Write-Host "源版本  : Python $SourceVersion"
Write-Host "目标目录: $Destination"

if (Test-Path -LiteralPath $Destination) {
    if (-not $Force) {
        throw "目标目录已存在：$Destination。如需重建，请加 -Force 参数。"
    }
    $resolvedDest = [System.IO.Path]::GetFullPath($Destination).TrimEnd('\')
    $expectedDest = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "runtime")).TrimEnd('\')
    if ($resolvedDest -ne $expectedDest) {
        throw "安全保护：-Force 只允许删除项目默认的 runtime 目录（$expectedDest），当前为 $resolvedDest。"
    }
    Write-Host "移除旧 runtime…" -ForegroundColor Yellow
    Remove-Item -LiteralPath $Destination -Recurse -Force
}

# ---------------------------------------------------------------------------
# 1. 复制解释器与运行库
# ---------------------------------------------------------------------------
Write-Step "复制 Python 解释器与运行库"
$null = New-Item -ItemType Directory -Force -Path $Destination
$rootFiles = @(
    "python.exe", "pythonw.exe", "python3.dll", "python312.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"
)
foreach ($name in $rootFiles) {
    $path = Join-Path $Source $name
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination (Join-Path $Destination $name) -Force
        Write-Host "  + $name"
    }
    else {
        Write-Host "  ! 缺少 $name（跳过）" -ForegroundColor Yellow
    }
}

$excludePyd = @(
    "_testbuffer.pyd", "_testcapi.pyd", "_testclinic.pyd", "_testconsole.pyd",
    "_testimportmultiple.pyd", "_testinternalcapi.pyd", "_testmultiphase.pyd",
    "_testsinglephase.pyd", "_ctypes_test.pyd"
)
Write-Step "复制 DLLs（排除测试扩展）"
Copy-Tree -SourceDir (Join-Path $Source "DLLs") -TargetDir (Join-Path $Destination "DLLs") -ExcludeFiles $excludePyd

# ---------------------------------------------------------------------------
# 2. 复制标准库（排除大包）与 Pillow / Tcl-Tk
# ---------------------------------------------------------------------------
Write-Step "复制 Lib 标准库（排除 test / site-packages 等无关包）"
$excludeLibDirs = @("site-packages", "test", "idlelib", "ensurepip", "lib2to3", "turtledemo", "__pycache__")
Copy-Tree -SourceDir (Join-Path $Source "Lib") -TargetDir (Join-Path $Destination "Lib") -ExcludeDirs $excludeLibDirs

Write-Step "复制 Pillow（Lib\site-packages\PIL）"
$pilSource = Join-Path $Source "Lib\site-packages\PIL"
$pilTarget = Join-Path $Destination "Lib\site-packages\PIL"
if (Test-Path -LiteralPath $pilSource) {
    Copy-Tree -SourceDir $pilSource -TargetDir $pilTarget -ExcludeDirs @("__pycache__")
}
else {
    Write-Host "  ! 未找到 Pillow，GUI 将无法读取贴图尺寸" -ForegroundColor Yellow
}
# 保留 dist-info 便于诊断（体积很小）
Get-ChildItem -LiteralPath (Join-Path $Source "Lib\site-packages") -Directory -Filter "pillow-*.dist-info" -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Tree -SourceDir $_.FullName -TargetDir (Join-Path $Destination "Lib\site-packages\$($_.Name)") -ExcludeDirs @("__pycache__")
}

Write-Step "复制 Tcl/Tk 运行时数据"
foreach ($tclDir in @("tcl8.6", "tk8.6")) {
    $srcTcl = Join-Path $Source "tcl\$tclDir"
    if (Test-Path -LiteralPath $srcTcl) {
        Copy-Tree -SourceDir $srcTcl -TargetDir (Join-Path $Destination "tcl\$tclDir") -ExcludeDirs @("__pycache__")
        Write-Host "  + tcl\$tclDir"
    }
    else {
        Write-Host "  ! 缺少 tcl\$tclDir，Tkinter 可能无法启动" -ForegroundColor Yellow
    }
}

# 空目录占位，保证 import site 时 site-packages 存在
$null = New-Item -ItemType Directory -Force -Path (Join-Path $Destination "Lib\site-packages")

# ---------------------------------------------------------------------------
# 3. 体积统计
# ---------------------------------------------------------------------------
Write-Step "统计 runtime 体积"
$allFiles = Get-ChildItem -LiteralPath $Destination -Recurse -File -ErrorAction SilentlyContinue
$totalBytes = ($allFiles | Measure-Object -Sum Length).Sum
if (-not $totalBytes) { $totalBytes = 0 }
$sizeMb = [math]::Round($totalBytes / 1MB, 1)
Write-Host ("  文件数 : {0}" -f $allFiles.Count)
Write-Host ("  总大小 : {0} MB" -f $sizeMb)

$reportLines = New-Object System.Collections.Generic.List[string]
$reportLines.Add("MSFS 2024 涂装工坊 —— 便携运行时构建报告")
$reportLines.Add("生成时间 : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$reportLines.Add("源目录   : $Source")
$reportLines.Add("源版本   : Python $SourceVersion")
$reportLines.Add("目标目录 : $Destination")
$reportLines.Add("文件数量 : $($allFiles.Count)")
$reportLines.Add("总大小   : $sizeMb MB")
$reportLines.Add("")
$reportLines.Add("顶层目录占用：")
foreach ($dir in (Get-ChildItem -LiteralPath $Destination -Directory)) {
    $dirBytes = (Get-ChildItem -LiteralPath $dir.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Sum Length).Sum
    $reportLines.Add(("  {0,-14} {1,10:N1} MB" -f $dir.Name, ($dirBytes / 1MB)))
}
$rootBytes = (Get-ChildItem -LiteralPath $Destination -File | Measure-Object -Sum Length).Sum
$reportLines.Add(("  {0,-14} {1,10:N1} MB" -f "(根目录文件)", ($rootBytes / 1MB)))
$reportLines -join "`r`n" | Set-Content -LiteralPath $ReportPath -Encoding utf8
Write-Host "  报告已写入: $ReportPath"

# ---------------------------------------------------------------------------
# 4. 验证
# ---------------------------------------------------------------------------
if ($SkipVerify) {
    Write-Step "跳过验证（-SkipVerify）"
    Write-Host "runtime 构建完成（未验证）: $Destination" -ForegroundColor Green
    exit 0
}

Write-Step "验证 1/3：import tkinter, PIL"
$check = Invoke-Native -FilePath $RuntimePython -Arguments @("-c", "import tkinter, PIL; print('OK', tkinter.TkVersion, PIL.__version__)") -WorkingDirectory $Destination
Write-Host $check.Output.Trim()
if ($check.ExitCode -ne 0) {
    Write-Host "验证失败：runtime 里的 tkinter/PIL 无法导入。" -ForegroundColor Red
    exit 1
}

Write-Step ("验证 2/3：启动 GUI（--smoke {0} 秒）" -f $GuiSeconds)
$smoke = Invoke-Native -FilePath $RuntimePython -Arguments @("-m", "liveryforge.gui.app", "--smoke", "$GuiSeconds") -WorkingDirectory $ProjectRoot
Write-Host $smoke.Output.Trim()
if ($smoke.ExitCode -ne 0) {
    Write-Host "验证失败：GUI 冒烟测试退出码 $($smoke.ExitCode)。" -ForegroundColor Red
    exit 1
}

Write-Step ("验证 3/3：Start-Process 启动 GUI，运行 {0} 秒后结束" -f $GuiSeconds)
$proc = Start-Process -FilePath $RuntimePython -ArgumentList @("-m", "liveryforge.gui.app") -WorkingDirectory $ProjectRoot -PassThru
Start-Sleep -Seconds $GuiSeconds
$proc.Refresh()
if ($proc.HasExited) {
    Write-Host "验证失败：GUI 进程提前退出，退出码 $($proc.ExitCode)。" -ForegroundColor Red
    exit 1
}
Stop-Process -Id $proc.Id -Force
Write-Host "GUI 进程已运行 ${GuiSeconds} 秒，未异常退出（已结束）。" -ForegroundColor Green

Write-Host ""
Write-Host "便携运行时构建成功！" -ForegroundColor Green
Write-Host "  运行时 : $RuntimePython"
Write-Host ("  体积   : {0} MB / {1} 个文件" -f $sizeMb, $allFiles.Count)
Write-Host "  启动   : 双击项目根目录的 run_gui.bat"
