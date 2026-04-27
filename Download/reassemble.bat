@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding windowsdesktop-runtime-9.0.3-win-x86.exe ---
copy /b windowsdesktop-runtime-9.0.3-win-x86.exe.part* windowsdesktop-runtime-9.0.3-win-x86.exe
if %errorlevel% equ 0 (
    echo Successfully created windowsdesktop-runtime-9.0.3-win-x86.exe
    del windowsdesktop-runtime-9.0.3-win-x86.exe.part*
    echo Deleted temporary parts for windowsdesktop-runtime-9.0.3-win-x86.exe
) else (
    echo ERROR: Failed to reassemble windowsdesktop-runtime-9.0.3-win-x86.exe. Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
