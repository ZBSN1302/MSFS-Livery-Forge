@echo off
chcp 65001 >nul
setlocal EnableExtensions
title MSFS 2024 涂装工坊

rem ============================================================
rem  MSFS 2024 涂装工坊 - 图形界面启动器
rem  优先使用项目自带的便携 Python（runtime\python.exe）；
rem  找不到时回退到系统的 python / py -3。
rem  保留控制台窗口，便于查看错误日志（关闭窗口即退出程序）。
rem ============================================================

cd /d "%~dp0"

set "PYEXE="
set "PYARGS="

if exist "%~dp0runtime\python.exe" set "PYEXE=%~dp0runtime\python.exe"

if not defined PYEXE (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYEXE set "PYEXE=%%I"
    )
)

if not defined PYEXE (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PYEXE=py"
        set "PYARGS=-3"
    )
)

if not defined PYEXE (
    echo.
    echo [错误] 未找到可用的 Python 运行时。
    echo.
    echo 解决方法（任选其一）：
    echo   1. 重新解压完整安装包，确认 runtime\python.exe 存在；
    echo   2. 从 python.org 安装 Python 3.10 - 3.12，安装时勾选 "Add python.exe to PATH"；
    echo   3. 打开命令行执行 python --version 检查系统里是否已有 Python。
    echo.
    pause
    exit /b 1
)

echo 正在启动 MSFS 2024 涂装工坊...
echo   运行时: %PYEXE% %PYARGS%
echo   提示  : 关闭本窗口即退出程序
echo.

if defined PYARGS (
    "%PYEXE%" %PYARGS% -m liveryforge.gui.app
) else (
    "%PYEXE%" -m liveryforge.gui.app
)

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [错误] 程序异常退出，退出码 %RC% 。
    echo 请把上面窗口里的日志发给开发者。
    echo 常见原因：Python 版本过低、缺少依赖库、杀毒软件拦截。
    echo.
    pause
)
endlocal & exit /b %RC%
