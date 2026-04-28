@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding smplayer-25.6.0-x64-unsigned.exe ---
copy /b smplayer-25.6.0-x64-unsigned.exe.part* smplayer-25.6.0-x64-unsigned.exe
if %errorlevel% equ 0 (
    echo Successfully created smplayer-25.6.0-x64-unsigned.exe
    del smplayer-25.6.0-x64-unsigned.exe.part*
    echo Deleted temporary parts for smplayer-25.6.0-x64-unsigned.exe
) else (
    echo ERROR: Failed to reassemble smplayer-25.6.0-x64-unsigned.exe. Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
