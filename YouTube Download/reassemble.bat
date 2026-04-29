@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "Why is CERN really making antimatter [jjp3WC8Unj8].mp4" ---
copy /b "Why is CERN really making antimatter [jjp3WC8Unj8].mp4.part*" "Why is CERN really making antimatter [jjp3WC8Unj8].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "Why is CERN really making antimatter [jjp3WC8Unj8].mp4"
    del "Why is CERN really making antimatter [jjp3WC8Unj8].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "Why is CERN really making antimatter [jjp3WC8Unj8].mp4". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
