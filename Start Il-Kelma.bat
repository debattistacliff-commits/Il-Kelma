@echo off
rem Start Il-Kelma quietly in the background. Kelma Buddy appears in the corner of the screen.
start "" /d "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -m ilkelma.app
