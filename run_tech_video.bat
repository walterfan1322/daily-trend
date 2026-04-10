@echo off
cd /d "%~dp0"
python auto_report_video.py --type tech >> auto_report_log.txt 2>&1
