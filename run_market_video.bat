@echo off
cd /d "%~dp0"
python auto_report_video.py --type market >> auto_report_log.txt 2>&1
