@echo off
setlocal EnableDelayedExpansion
title 多 Agent 对抗辩论模拟 · 启动器
REM ============================================================
REM  用法：
REM    start.bat            真模型模式（后端 8000 + 前端 5173，读 backend\.env）
REM    start.bat demo       演示模式（FakeLLM 零 token，后端 8010 + 前端 5174）
REM    start.bat stop       停止全部服务并清理端口
REM ============================================================
set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=real"

if /i "%MODE%"=="stop" goto stop_all

REM ---------- 0) 基础检查 ----------
if not exist "%BACKEND%\.venv\Scripts\python.exe" (
  echo [ERROR] 未找到后端虚拟环境。请先执行: cd backend ^&^& uv sync
  pause
  exit /b 1
)
if not exist "%BACKEND%\.env" (
  echo [ERROR] 未找到 backend\.env。请先复制 .env.example 并填入 API Key。
  pause
  exit /b 1
)

REM ---------- 1) MySQL 检查 ----------
netstat -ano | findstr ":3306" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [WARN] MySQL 未在 3306 监听，尝试启动服务 MySQL80...
  net start MySQL80 >nul 2>&1
  ping -n 3 127.0.0.1 >nul
  netstat -ano | findstr ":3306" | findstr "LISTENING" >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] MySQL 仍未运行，请手动启动 MySQL 服务后重试。
    pause
    exit /b 1
  )
)
echo [OK] MySQL 运行中

REM ---------- 2) 端口规划与残留清理 ----------
if /i "%MODE%"=="demo" (
  set "BE_PORT=8010"
  set "FE_PORT=5174"
  set "BE_TITLE=debate-backend-demo"
  set "FE_TITLE=debate-frontend-demo"
) else (
  set "MODE=real"
  set "BE_PORT=8000"
  set "FE_PORT=5173"
  set "BE_TITLE=debate-backend"
  set "FE_TITLE=debate-frontend"
)
call :cleanport !BE_PORT!
call :cleanport !FE_PORT!

REM ---------- 3) 启动后端 ----------
cd /d "%BACKEND%"
if "%MODE%"=="demo" (
  start "!BE_TITLE!" cmd /k .venv\Scripts\python.exe scripts\dev_uvicorn.py !BE_PORT!
) else (
  start "!BE_TITLE!" cmd /k .venv\Scripts\python.exe -m uvicorn debate.api.app:create_app --factory --port !BE_PORT! --log-level warning
)
echo [..] 后端启动中（模式：%MODE%，端口 !BE_PORT!）...

REM ---------- 4) 等待后端健康 ----------
set /a TRIES=0
:wait_backend
ping -n 2 127.0.0.1 >nul
curl -s -o nul "http://127.0.0.1:!BE_PORT!/api/health" 2>nul
if not errorlevel 1 goto backend_ok
set /a TRIES+=1
if !TRIES! lss 40 goto wait_backend
echo [ERROR] 后端 40 秒未就绪，请查看窗口（!BE_TITLE!）的报错。
pause
exit /b 1
:backend_ok
echo [OK] 后端就绪

REM ---------- 5) 启动前端 ----------
cd /d "%FRONTEND%"
if not exist "node_modules" (
  echo [..] 首次运行：安装前端依赖（约 1-2 分钟）...
  call npm install --no-fund --no-audit
)
if "%MODE%"=="demo" (
  start "!FE_TITLE!" cmd /k set DEV_API_TARGET=http://127.0.0.1:!BE_PORT!&& npx vite --port !FE_PORT! --strictPort
) else (
  start "!FE_TITLE!" cmd /k npx vite --port !FE_PORT! --strictPort
)
echo [..] 前端启动中（端口 !FE_PORT!）...
ping -n 6 127.0.0.1 >nul

REM ---------- 6) 收尾 ----------
start "" "http://localhost:!FE_PORT!/"
echo.
echo  ============================================================
echo   启动完成（%MODE% 模式）
echo   前端页面   http://localhost:!FE_PORT!
echo   后端接口   http://127.0.0.1:!BE_PORT!/api/docs
echo   停止全部   关闭两个服务窗口，或运行  start.bat stop
echo  ============================================================
echo.
echo 本窗口可以关闭；两个服务窗口请保持打开。
pause
exit /b 0

REM ============================================================
:stop_all
echo [..] 停止全部服务...
call :cleanport 8000
call :cleanport 5173
call :cleanport 8010
call :cleanport 5174
echo [OK] 已清理端口 8000/5173/8010/5174。MySQL 服务保持运行。
pause
exit /b 0

REM ---------- 端口清理子过程 ----------
:cleanport
set "CP_PORT=%~1"
:cp_loop
set "CP_PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":!CP_PORT!" ^| findstr "LISTENING"') do (
  if not defined CP_PID set "CP_PID=%%p"
)
if not defined CP_PID goto cp_done
echo [..] 端口 !CP_PORT! 被 PID !CP_PID! 占用，结束该进程...
taskkill /PID !CP_PID! /F >nul 2>&1
ping -n 2 127.0.0.1 >nul
goto cp_loop
:cp_done
exit /b 0
