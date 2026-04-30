@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling NuGet Offline Feed...
echo ========================================
echo.
echo Combining NuGetOfflineFeed.part* into NuGetOfflineFeed.zip...
copy /b NuGetOfflineFeed.part* NuGetOfflineFeed.zip
if %errorlevel% equ 0 (
    echo.
    echo Successfully created NuGetOfflineFeed.zip
    echo.
    echo Deleting temporary parts...
    del NuGetOfflineFeed.part*
    echo Done! You can now extract NuGetOfflineFeed.zip
) else (
    echo.
    echo ERROR: Failed to reassemble the ZIP file.
    echo All parts are kept intact so you can retry.
)
echo.
echo ========================================
pause
