@echo off
setlocal
set "ROJO_EXE="

for /f "delims=" %%I in ('where rojo 2^>nul') do if not defined ROJO_EXE set "ROJO_EXE=%%I"

if not defined ROJO_EXE (
    echo Rojo was not found.
    echo Install Rojo and make sure rojo.exe is available on PATH.
    pause
    exit /b 1
)

echo Watching Roblox Studio plugin source...
echo Keep this window open while developing. Press Ctrl+C to stop.
echo.
if not exist "%LOCALAPPDATA%\Roblox\Plugins" mkdir "%LOCALAPPDATA%\Roblox\Plugins"
"%ROJO_EXE%" build "%~dp0roblox_plugin\default.project.json" --output "%LOCALAPPDATA%\Roblox\Plugins\RobloxPrimitiveSync-Studio.rbxm" --watch
pause
