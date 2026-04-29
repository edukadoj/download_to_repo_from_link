@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "_ [GxFzURMqzhU].mp4" ---
copy /b "_ [GxFzURMqzhU].mp4.part*" "_ [GxFzURMqzhU].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "_ [GxFzURMqzhU].mp4"
    del "_ [GxFzURMqzhU].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "_ [GxFzURMqzhU].mp4". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
