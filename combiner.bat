@echo off
setlocal enabledelayedexpansion

:: Root folder = folder where this batch file is located (trailing backslash included)
set "root=%CD%\"
set "outfile=%root%combined.txt"

:: Delete any old combined.txt to start fresh
if exist "%outfile%" del "%outfile%"

:: List of file extensions to treat as text (customise as needed)
set "extensions=.py .cs .txt .html .css .js .java .cpp .c .h .hpp .xml .xaml .md .yml .yaml .ini .cfg .cmd"

:: Walk every file in the folder and subfolders
for /R %%F in (*) do (
    set "full=%%F"
    set "ext=%%~xF"
    set "include=0"

    :: Check if the current file's extension is in our list
    for %%E in (%extensions%) do (
        if /i "!ext!"=="%%E" set "include=1"
    )

    if !include! equ 1 (
        :: Skip the batch file itself and the output file
        if not "!full!"=="%~f0" (
            if not "!full!"=="%outfile%" (
                :: Create a relative path with forward slashes (e.g., B/b1.cs)
                set "rel=!full:%root%=!"
                set "rel=!rel:\=/!"

                echo !rel!:>> "%outfile%"
                type "!full!">> "%outfile%"
                echo.>> "%outfile%"
            )
        )
    )
)

echo Done! Combined file created: %outfile%
pause