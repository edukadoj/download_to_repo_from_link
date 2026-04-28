@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding 23_04_2026 [cle-45adu50].mp4 ---
copy /b 23_04_2026 [cle-45adu50].mp4.part* 23_04_2026 [cle-45adu50].mp4
if %errorlevel% equ 0 (
    echo Successfully created 23_04_2026 [cle-45adu50].mp4
    del 23_04_2026 [cle-45adu50].mp4.part*
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble 23_04_2026 [cle-45adu50].mp4. Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
