@echo off
rem Launches the Dungeons & Dynasties save editor.
rem Drag a backup .json onto this file to open it directly.

set "HERE=%~dp0"

where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
    start "" pythonw "%HERE%dd_editor.pyw" %1
    exit /b 0
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    start "" python "%HERE%dd_editor.pyw" %1
    exit /b 0
)

echo Could not find Python on your PATH.
echo Install it from https://www.python.org/downloads/ and try again.
pause
exit /b 1
