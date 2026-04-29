@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "پول ایرانی‌ها برای لبنانی‌ها [LfMDkHc7178].mp4" ---
copy /b "پول ایرانی‌ها برای لبنانی‌ها [LfMDkHc7178].mp4.part*" "پول ایرانی‌ها برای لبنانی‌ها [LfMDkHc7178].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "پول ایرانی‌ها برای لبنانی‌ها [LfMDkHc7178].mp4"
    del "پول ایرانی‌ها برای لبنانی‌ها [LfMDkHc7178].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "پول ایرانی‌ها برای لبنانی‌ها [LfMDkHc7178].mp4". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
