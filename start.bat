@echo off
REM Windows shell for run.py. All logic lives in run.py; see docs/RUNNING.md.
REM ASCII only on purpose: cmd.exe reads .bat files in the OEM code page,
REM and this file must also stay CRLF (.gitattributes enforces both).

setlocal
cd /d "%~dp0"

REM Prefer the "py" launcher: plain "python" on Windows is often the
REM Microsoft Store stub, which opens the Store instead of running anything.
REM "py -3" picks the newest installed Python 3.x.
where py >nul 2>nul
if errorlevel 1 goto :plain

py -3 run.py %*
goto :done

:plain
python run.py %*

:done
REM Keep the window open when it failed, so a double-click shows the reason.
if errorlevel 1 pause
endlocal
