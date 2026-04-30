@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding "2024-09-10 The Lost Art of Terminal Web Browsing [dLeel2Bq8ps].mp4" ---
copy /b "2024-09-10 The Lost Art of Terminal Web Browsing [dLeel2Bq8ps].mp4.part*" "2024-09-10 The Lost Art of Terminal Web Browsing [dLeel2Bq8ps].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "2024-09-10 The Lost Art of Terminal Web Browsing [dLeel2Bq8ps].mp4"
    del "2024-09-10 The Lost Art of Terminal Web Browsing [dLeel2Bq8ps].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "2024-09-10 The Lost Art of Terminal Web Browsing [dLeel2Bq8ps].mp4". Parts kept intact.
)
echo --- Rebuilding "2023-11-16 No GUI No Problem! How to Quickly Browse the Web in your Linux Terminal [tak4HeqwmYU].mp4" ---
copy /b "2023-11-16 No GUI No Problem! How to Quickly Browse the Web in your Linux Terminal [tak4HeqwmYU].mp4.part*" "2023-11-16 No GUI No Problem! How to Quickly Browse the Web in your Linux Terminal [tak4HeqwmYU].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "2023-11-16 No GUI No Problem! How to Quickly Browse the Web in your Linux Terminal [tak4HeqwmYU].mp4"
    del "2023-11-16 No GUI No Problem! How to Quickly Browse the Web in your Linux Terminal [tak4HeqwmYU].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "2023-11-16 No GUI No Problem! How to Quickly Browse the Web in your Linux Terminal [tak4HeqwmYU].mp4". Parts kept intact.
)
echo "2022-01-25 How to Surf the Web in Terminal (with a text-based browser) [rbYHH7UJE3Y].mp4" was downloaded as a single file (no chunks).
echo "2025-06-12 Control Gmail from a terminal! (Send and receive email in PowershellBash) [suzg8TvM2_U].mp4" was downloaded as a single file (no chunks).
echo --- Rebuilding "2021-03-23 Watching YouTube a New Way!! Use a Console  Terminal to Search YouTube on Linux  Mac (ytfzf) [-lQabXik_6I].mp4" ---
copy /b "2021-03-23 Watching YouTube a New Way!! Use a Console  Terminal to Search YouTube on Linux  Mac (ytfzf) [-lQabXik_6I].mp4.part*" "2021-03-23 Watching YouTube a New Way!! Use a Console  Terminal to Search YouTube on Linux  Mac (ytfzf) [-lQabXik_6I].mp4"
if %errorlevel% equ 0 (
    echo Successfully created "2021-03-23 Watching YouTube a New Way!! Use a Console  Terminal to Search YouTube on Linux  Mac (ytfzf) [-lQabXik_6I].mp4"
    del "2021-03-23 Watching YouTube a New Way!! Use a Console  Terminal to Search YouTube on Linux  Mac (ytfzf) [-lQabXik_6I].mp4.part*"
    echo Deleted temporary parts
) else (
    echo ERROR: Failed to reassemble "2021-03-23 Watching YouTube a New Way!! Use a Console  Terminal to Search YouTube on Linux  Mac (ytfzf) [-lQabXik_6I].mp4". Parts kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
