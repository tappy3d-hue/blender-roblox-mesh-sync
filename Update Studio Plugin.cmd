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

if not exist "%LOCALAPPDATA%\Roblox\Plugins" mkdir "%LOCALAPPDATA%\Roblox\Plugins"
"%ROJO_EXE%" build "%~dp0roblox_plugin\default.project.json" --output "%LOCALAPPDATA%\Roblox\Plugins\RobloxPrimitiveSync-Studio.rbxm"
if errorlevel 1 (
    echo.
    echo Studio plugin update failed.
    pause
    exit /b 1
)

echo.
echo The currently loaded RobloxPrimitiveSync Studio plugin was updated in place.
echo Studio can reload it automatically when "Reload plugins on file changed" is enabled.
timeout /t 3 >nul
