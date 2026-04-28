@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "I_hacked_MKBHD_s_locked_phone [PPJ6NJkmDAo].mp4" ---
copy /b "I_hacked_MKBHD_s_locked_phone [PPJ6NJkmDAo].mp4.part*" "I_hacked_MKBHD_s_locked_phone [PPJ6NJkmDAo].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "I_hacked_MKBHD_s_locked_phone [PPJ6NJkmDAo].mp4"
    del "I_hacked_MKBHD_s_locked_phone [PPJ6NJkmDAo].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "I_hacked_MKBHD_s_locked_phone [PPJ6NJkmDAo].mp4". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
