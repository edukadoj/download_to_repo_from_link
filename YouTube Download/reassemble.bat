@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "2026-04-30 آکسیوس آماده شدن ترامپ برای حمله برق‌آسا پوتین ترامپ را از حمله زمینی برحذر داشت [zeWStQmKvtE].mp4" ---
copy /b "2026-04-30 آکسیوس آماده شدن ترامپ برای حمله برق‌آسا پوتین ترامپ را از حمله زمینی برحذر داشت [zeWStQmKvtE].mp4.part*" "2026-04-30 آکسیوس آماده شدن ترامپ برای حمله برق‌آسا پوتین ترامپ را از حمله زمینی برحذر داشت [zeWStQmKvtE].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "2026-04-30 آکسیوس آماده شدن ترامپ برای حمله برق‌آسا پوتین ترامپ را از حمله زمینی برحذر داشت [zeWStQmKvtE].mp4"
    del "2026-04-30 آکسیوس آماده شدن ترامپ برای حمله برق‌آسا پوتین ترامپ را از حمله زمینی برحذر داشت [zeWStQmKvtE].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "2026-04-30 آکسیوس آماده شدن ترامپ برای حمله برق‌آسا پوتین ترامپ را از حمله زمینی برحذر داشت [zeWStQmKvtE].mp4". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
