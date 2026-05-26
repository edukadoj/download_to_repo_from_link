@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo  Reassembling all files from chunks...
echo ========================================

:: Process each distinct base name (looking for the first chunk .part0000)
for %%f in ("*.part0000") do (
    set "full=%%f"
    set "base=!full:.part0000=!"

    if not defined _processed_!base! (
        set "_processed_!base!=1"

        echo.
        echo --- Rebuilding "!base!" ---
        if exist "!base!.part*" (
            copy /b "!base!.part*" "!base!" >nul
            if !errorlevel! equ 0 (
                echo Successfully created "!base!"
                del "!base!.part*" 2>nul
                echo Deleted temporary parts.
            ) else (
                echo ERROR: Failed to reassemble "!base!".
            )
        ) else (
            echo WARNING: No parts found for "!base!".
        )
    )
)

echo.
echo ========================================
echo All done! Press any key to exit.
pause >nul
