@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding video_1.webm ---
copy /b video_1.webm.part* video_1.webm
if %errorlevel% equ 0 (
    echo Successfully created video_1.webm
    del video_1.webm.part*
    echo Deleted temporary parts for video_1.webm
) else (
    echo ERROR: Failed to reassemble video_1.webm. Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
