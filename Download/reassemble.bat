@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding gfx_win_101.2141.exe ---
copy /b gfx_win_101.2141.exe.part* gfx_win_101.2141.exe
if %errorlevel% equ 0 (
    echo Successfully created gfx_win_101.2141.exe
    del gfx_win_101.2141.exe.part*
    echo Deleted temporary parts for gfx_win_101.2141.exe
) else (
    echo ERROR: Failed to reassemble gfx_win_101.2141.exe. Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
