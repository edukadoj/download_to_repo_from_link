@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling Visual Studio Layout...
echo ========================================
echo.
echo Combining VsCommunity2026Layout.part* into VsCommunity2026Layout.zip...
copy /b VsCommunity2026Layout.part* VsCommunity2026Layout.zip
if %errorlevel% equ 0 (
    echo.
    echo Successfully created VsCommunity2026Layout.zip
    echo.
    echo Deleting temporary parts...
    del VsCommunity2026Layout.part*
    echo Done! You can now extract VsCommunity2026Layout.zip and run vs_setup.exe
) else (
    echo.
    echo ERROR: Failed to reassemble the ZIP file.
    echo All parts are kept intact so you can retry.
)
echo.
echo ========================================
pause
