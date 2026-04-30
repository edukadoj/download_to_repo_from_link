@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "2026-04-30 چرا احتمال ازسرگیری جنگ دوباره افزایش یافت؟ [kcrUXjxhd4A].mp4" ---
copy /b "2026-04-30 چرا احتمال ازسرگیری جنگ دوباره افزایش یافت؟ [kcrUXjxhd4A].mp4.part*" "2026-04-30 چرا احتمال ازسرگیری جنگ دوباره افزایش یافت؟ [kcrUXjxhd4A].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "2026-04-30 چرا احتمال ازسرگیری جنگ دوباره افزایش یافت؟ [kcrUXjxhd4A].mp4"
    del "2026-04-30 چرا احتمال ازسرگیری جنگ دوباره افزایش یافت؟ [kcrUXjxhd4A].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "2026-04-30 چرا احتمال ازسرگیری جنگ دوباره افزایش یافت؟ [kcrUXjxhd4A].mp4". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
