@echo off
rem Codex usage floating widget (no console window)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0codex_quota_widget.pyw"
) else (
  echo pythonw.exe not found in PATH. Please install Python and add it to PATH.
  pause
)
