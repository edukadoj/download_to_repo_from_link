@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "downloaded_file_1777517632.bin" ---
copy /b "downloaded_file_1777517632.bin.part*" "downloaded_file_1777517632.bin"
if %errorlevel% equ 0 (
    echo Successfully created "downloaded_file_1777517632.bin"
    del "downloaded_file_1777517632.bin.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "downloaded_file_1777517632.bin". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul