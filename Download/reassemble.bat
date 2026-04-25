@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  Reassembling all downloaded files...
echo ========================================
echo --- Rebuilding 582.28-notebook-win10-win11-64bit-international-dch-whql.exe ---
copy /b 582.28-notebook-win10-win11-64bit-international-dch-whql.exe.part* 582.28-notebook-win10-win11-64bit-international-dch-whql.exe
if %errorlevel% equ 0 (
    echo Successfully created 582.28-notebook-win10-win11-64bit-international-dch-whql.exe
    del 582.28-notebook-win10-win11-64bit-international-dch-whql.exe.part*
    echo Deleted temporary parts for 582.28-notebook-win10-win11-64bit-international-dch-whql.exe
) else (
    echo ERROR: Failed to reassemble 582.28-notebook-win10-win11-64bit-international-dch-whql.exe. Part files kept intact.
)
echo --- Rebuilding SP000774.exe ---
copy /b SP000774.exe.part* SP000774.exe
if %errorlevel% equ 0 (
    echo Successfully created SP000774.exe
    del SP000774.exe.part*
    echo Deleted temporary parts for SP000774.exe
) else (
    echo ERROR: Failed to reassemble SP000774.exe. Part files kept intact.
)
echo ========================================
echo All done! Press any key to exit.
pause >nul
