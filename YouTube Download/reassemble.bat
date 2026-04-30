@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "_ [eaQO1zxbT-s].mp4" ---
copy /b "_ [eaQO1zxbT-s].mp4.part*" "_ [eaQO1zxbT-s].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "_ [eaQO1zxbT-s].mp4"
    del "_ [eaQO1zxbT-s].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "_ [eaQO1zxbT-s].mp4". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
