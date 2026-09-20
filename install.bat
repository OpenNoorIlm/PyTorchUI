@echo off
REM PyTorchUI installer for Windows CMD.
REM
REM Downloads install.ps1 and runs it.  Same effect as:
REM     irm https://raw.githubusercontent.com/OpenNoorIlm/PyTorchUI/main/install.ps1 | iex
REM
REM Usage:
REM     curl -o install.bat https://raw.githubusercontent.com/OpenNoorIlm/PyTorchUI/main/install.bat
REM     install.bat

setlocal

echo PyTorchUI installer
echo Fetching install.ps1 ...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$u='https://raw.githubusercontent.com/OpenNoorIlm/PyTorchUI/main/install.ps1';" ^
    "iwr -useb $u | iex"

if %errorlevel% neq 0 (
    echo.
    echo Installation failed.  See the output above.
    exit /b %errorlevel%
)

endlocal
