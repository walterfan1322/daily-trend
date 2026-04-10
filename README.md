# Daily Trend

Automated daily news aggregation and AI-powered video generation system. Collects RSS feeds for configurable topics, summarizes them using MiniMax AI, optionally generates videos via MoneyPrinter, and delivers notifications through Telegram.

## Features

- **Multi-Topic RSS Aggregation**: KPOP, Anime, Tech News, International News, Market Reports
- **AI Summarization**: Chinese-language daily digests powered by MiniMax API (MiniMax-M2)
- **Video Generation**: Converts digests into videos using MoneyPrinter
- **Telegram Notifications**: Push summaries directly to Telegram
- **Multi-Machine Orchestration**: Coordinates across multiple machines via SSH/SFTP
- **Smart Deduplication**: Normalizes titles to filter duplicate news across sources
- **Scheduled Automation**: Windows Task Scheduler integration for hands-free operation

## Architecture

```
RSS Feeds → daily_trend.py → MiniMax AI Summary → Telegram Notification
                                    ↓
                           MoneyPrinter (video)
                                    ↓
                        download_videos.py (SSH/SFTP)
                                    ↓
                          sync_videos.py (local sync)
```

## Setup

### Prerequisites

```bash
pip install feedparser paramiko
```

### Configuration

1. Copy `config.example.json` to `config.json`
2. Fill in required fields:
   - `minimax_api_key` — MiniMax API key
   - `telegram_bot_token` — Telegram Bot token from BotFather
   - `telegram_chat_id` — Your Telegram chat ID
   - `data_dir` — Local data directory path
3. Set environment variables for remote connections:
   - `MP_HOST`, `MP_USER` — MoneyPrinter server
   - `MAC_HOST`, `MAC_USER` — Mac Mini (for reports)
   - `MONEYPRINTER_URL` — MoneyPrinter web URL

### Running

```bash
# Run all enabled topics
python daily_trend.py

# Run specific topic
python daily_trend.py --topic kpop

# Test mode (skips Telegram)
python daily_trend.py --test

# Generate video from daily report
python auto_report_video.py --type tech

# Download generated videos
python download_videos.py

# Sync videos to local drive
python sync_videos.py
```

### Windows Task Scheduler

```bash
setup_task.bat   # Creates DailyTrendCollector scheduled at 09:00
```

## Project Structure

```
daily_trend/
├── daily_trend.py           # Main RSS collector + AI summarizer
├── auto_report_video.py     # Video generation from daily reports
├── download_videos.py       # Download videos from MoneyPrinter server
├── sync_videos.py           # Sync videos to local drive
├── migrate_to_hp3.py        # Migration script for moving tasks between machines
├── config.example.json      # Configuration template
├── setup_task.bat           # Windows Task Scheduler setup
├── run_daily.bat            # Execute daily collection
├── run_download.bat         # Execute video download
├── run_market_video.bat     # Execute market report video
├── run_news_video.bat       # Execute news report video
└── run_tech_video.bat       # Execute tech report video
```

## Topic Configuration

Each topic in `config.json` supports:
- Multiple RSS feed sources (English and Chinese)
- Custom AI summarization prompts
- Enable/disable toggle
- Optional video generation flag
- Display name for Telegram messages
