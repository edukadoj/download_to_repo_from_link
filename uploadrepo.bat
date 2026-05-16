@echo off
cd /d "%~dp0"

set "REPO_URL=https://github.com/edukadoj/download_to_repo_from_link.git"

:: 1. Make it a git repository (if not already)
git init

:: 2. Set your identity (only for this repo)
git config user.email "edukadoj@users.noreply.github.com"
git config user.name "edukadoj"

:: 3. Add or update the remote URL
git remote add origin %REPO_URL% 2>nul
git remote set-url origin %REPO_URL% 2>nul

:: 4. Switch to (or create) the 'main' branch
git checkout -B main

:: 5. Add all files – including deletions, hidden folders, workflows
git add --all

:: 6. Commit (simple message – no special characters)
git commit -m "Force upload"

:: 7. Force push to overwrite remote
git push --force origin main

echo Done.
pause