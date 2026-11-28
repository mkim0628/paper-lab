@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
where git >nul 2>&1
if errorlevel 1 goto :nogit
if not exist ".git" goto :nogit
echo [Paper Lab] 최신 버전을 받는 중...
git pull --ff-only
if errorlevel 1 goto :fail
call start.bat %*
exit /b %errorlevel%

:nogit
echo.
echo [Paper Lab] git 으로 받은 폴더가 아니어서 자동 업데이트를 할 수 없습니다.
echo             GitHub 에서 최신 ZIP 을 받아 이 폴더에 덮어쓰세요. data 폴더와 paperlab.env 는 그대로 유지됩니다.
pause
exit /b 1

:fail
echo.
echo [Paper Lab] 업데이트에 실패했습니다. 위 메시지를 확인하세요. 수정한 파일이 있으면 충돌할 수 있습니다.
pause
exit /b 1
