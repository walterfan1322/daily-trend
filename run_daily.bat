@echo off
cd /d "C:\DailyTrend"
"C:\Program Files\Python312\python.exe" daily_trend.py >> "C:\DailyTrend\logs\daily_trend.log" 2>&1
