@echo off
cd /d "%~dp0" || exit /b 1
pdm lock --check || exit /b 1
pdm run python scripts/release_workspace.py windows %*
exit /b %errorlevel%
