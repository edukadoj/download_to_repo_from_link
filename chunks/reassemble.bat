@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
for %%f in ("*.part0000") do (
    set "full=%%f"
    set "base=!full:.part0000=!"
    if not defined _processed_!base! (
        set "_processed_!base!=1"
        copy /b "!base!.part*" "!base!" >nul
        del "!base!.part*" 2>nul
    )
)
