@echo off
chcp 65001 >nul
setlocal EnableExtensions
title MSFS 2024 涂装工坊 - 命令行

rem ============================================================
rem  MSFS 2024 涂装工坊 - 命令行启动器
rem  用法示例：
rem    run_cli.bat list-profiles
rem    run_cli.bat detect
rem    run_cli.bat convert samples\textures -o 输出\贴图 --format both
rem    run_cli.bat build --profile longitude --textures samples\textures -o 输出 --title "我的座机"
rem    run_cli.bat clone --template "E:\历史\Community\...\livery" --textures samples\textures -o 输出 --title "克隆"
rem  不带参数运行会显示帮助。优先使用 runtime\python.exe。
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
    echo   2. 从 python.org 安装 Python 3.10 - 3.12，安装时勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

if defined PYARGS (
    "%PYEXE%" %PYARGS% -m liveryforge %*
) else (
    "%PYEXE%" -m liveryforge %*
)

set "RC=%ERRORLEVEL%"

rem 双击运行（没有参数）时暂停，避免窗口一闪而过
if "%~1"=="" (
    echo.
    pause
)
endlocal & exit /b %RC%
