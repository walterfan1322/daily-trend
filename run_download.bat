@echo off
cd /d "%~dp0"
python download_videos.py >> "%~dp0\download_log.txt" 2>&1
