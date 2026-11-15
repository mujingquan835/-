@echo off
cd /d "%~dp0"
python radar.py all -n 40 >> data\run.log 2>&1
