@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding vlc-3.0.23-win64.exe ---
copy /b vlc-3.0.23-win64.exe.part* vlc-3.0.23-win64.exe
if %errorlevel% equ 0 (
    echo Successfully created vlc-3.0.23-win64.exe
    del vlc-3.0.23-win64.exe.part*
    echo Deleted temporary parts for vlc-3.0.23-win64.exe
) else (
    echo ERROR: Failed to reassemble vlc-3.0.23-win64.exe. Parts kept intact.
)
echo download was downloaded as a single file (no chunks).
echo ========================================
echo All done! Press any key to exit.
pause >nul
