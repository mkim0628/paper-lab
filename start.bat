@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
title Paper Lab

call :findpy
if defined PY goto :run

echo.
echo [Paper Lab] Python 3.10 이상이 필요합니다. winget 으로 자동 설치를 시도합니다...
where winget >nul 2>&1
if errorlevel 1 goto :manual
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :findpy
if defined PY goto :run
echo.
echo [Paper Lab] Python 설치는 끝났지만 이 창에서 찾지 못했습니다.
echo             이 창을 닫고 start.bat 을 다시 더블클릭해 주세요.
pause
exit /b 1

:manual
echo.
echo [Paper Lab] winget 을 쓸 수 없습니다. 아래 페이지에서 Python 3.12 를 설치해 주세요.
echo             설치 첫 화면에서 "Add python.exe to PATH" 를 꼭 체크한 뒤 start.bat 을 다시 실행하세요.
start "" https://www.python.org/downloads/
pause
exit /b 1

:run
"%PY%" scripts\launcher.py %*
if errorlevel 1 pause
exit /b %errorlevel%

:findpy
set "PY="
for %%C in (py python) do (
  if not defined PY (
    %%C -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=%%C"
  )
)
if defined PY exit /b 0
for %%V in (313 312 311 310) do (
  if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
)
exit /b 0
