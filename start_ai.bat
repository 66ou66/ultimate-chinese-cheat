@echo off
chcp 65001 >nul
cd /d "%~dp0"
"ai象棋外挂项目pro - 副本\.venv\Scripts\python.exe" main_merged.py
pause