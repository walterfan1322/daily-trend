@echo off
echo Creating Windows Task Scheduler job for Daily Trend Collector...
schtasks /create /tn "DailyTrendCollector" /tr "C:\DailyTrend\run_daily.bat" /sc daily /st 09:00 /f
echo.
echo Task created! Will run daily at 09:00.
echo To run manually: schtasks /run /tn "DailyTrendCollector"
echo To delete: schtasks /delete /tn "DailyTrendCollector" /f
pause
