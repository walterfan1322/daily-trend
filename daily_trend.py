#!/usr/bin/env python3
"""
Daily Trend Collector
=====================
Automatically fetches news from RSS feeds for configured topics (KPOP, Anime, etc.),
summarizes them via MiniMax API, saves daily reports as txt files,
and sends a Telegram push notification.

Usage:
    python daily_trend.py              # Run all enabled topics
    python daily_trend.py --topic kpop # Run specific topic
    python daily_trend.py --test       # Test run (no Telegram push)
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

# ---------------------------------------------------------------------------
# Third-party imports (installed via pip)
# ---------------------------------------------------------------------------
try:
    import feedparser
except ImportError:
    print("[ERROR] feedparser not installed. Run: pip install feedparser")
    sys.exit(1)

try:
    import paramiko
except ImportError:
    paramiko = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
MINIMAX_API_URL = "https://api.minimax.io/v1/chat/completions"
MINIMAX_MODEL = "MiniMax-M2"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MSG_LIMIT = 4096  # Telegram message character limit
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DailyTrendBot/1.0"
TW = timezone(timedelta(hours=8))


def load_config() -> dict:
    """Load configuration from config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def log(msg: str):
    """Print a timestamped log message."""
    ts = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


# ---------------------------------------------------------------------------
# RSS Feed Fetching
# ---------------------------------------------------------------------------
def fetch_feed(feed_url: str, feed_name: str, hours_back: int = 28) -> list[dict]:
    """Fetch and parse an RSS feed, returning entries from the last N hours."""
    log(f"  Fetching {feed_name}...")
    try:
        req = urllib_request.Request(feed_url, headers={"User-Agent": USER_AGENT})
        with urllib_request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        parsed = feedparser.parse(raw)
    except Exception as e:
        log(f"  [WARN] Failed to fetch {feed_name}: {e}")
        return []

    cutoff = datetime.now(TW) - timedelta(hours=hours_back)
    entries = []

    for entry in parsed.entries:
        # Parse published date
        pub_date = None
        for date_field in ("published_parsed", "updated_parsed"):
            tp = getattr(entry, date_field, None)
            if tp:
                try:
                    pub_date = datetime(*tp[:6], tzinfo=timezone.utc).astimezone(TW)
                except Exception:
                    pass
                break

        # If no date or too old, skip
        if pub_date and pub_date < cutoff:
            continue

        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        summary = getattr(entry, "summary", "")
        # Clean HTML tags from summary
        summary = re.sub(r"<[^>]+>", "", summary).strip()
        summary = re.sub(r"\s+", " ", summary)[:300]

        if not title:
            continue

        entries.append({
            "source": feed_name,
            "title": title,
            "link": link,
            "summary": summary,
            "published": pub_date.strftime("%Y-%m-%d %H:%M") if pub_date else "",
        })

    log(f"  {feed_name}: {len(entries)} entries (last {hours_back}h)")
    return entries


def collect_all_feeds(feeds: list[dict], hours_back: int = 28) -> list[dict]:
    """Collect entries from all feeds for a topic."""
    all_entries = []
    seen_titles = set()

    for feed_cfg in feeds:
        entries = fetch_feed(feed_cfg["url"], feed_cfg["name"], hours_back)
        for entry in entries:
            # Simple dedup by normalized title
            norm_title = re.sub(r"\s+", "", entry["title"].lower())
            if norm_title not in seen_titles:
                seen_titles.add(norm_title)
                all_entries.append(entry)

    # Sort by published date (newest first)
    all_entries.sort(key=lambda x: x.get("published", ""), reverse=True)
    return all_entries


# ---------------------------------------------------------------------------
# MiniMax Summarization
# ---------------------------------------------------------------------------
def summarize_with_minimax(entries: list[dict], topic_config: dict, api_key: str) -> str:
    """Use MiniMax API to summarize collected entries into a daily digest."""
    if not entries:
        return "今天沒有找到新的相關新聞。"

    if not api_key:
        log("  [WARN] No MiniMax API key, returning raw list")
        return _format_raw_list(entries)

    # Build the input text from entries (limit to top 30 to save tokens)
    news_text = ""
    for i, entry in enumerate(entries[:30], 1):
        news_text += f"\n{i}. [{entry['source']}] {entry['title']}"
        if entry.get("summary"):
            news_text += f"\n   {entry['summary'][:150]}"
        if entry.get("published"):
            news_text += f"\n   ({entry['published']})"
        news_text += "\n"

    system_prompt = topic_config.get("summary_prompt", "請將以下新聞整理成中文每日摘要。")
    system_prompt += """

格式要求：
1. 用繁體中文
2. 挑出 5-8 則最重要/最有趣的新聞
3. 每則格式：
   【標題】
   摘要（2-3句話）

4. 最後加一段「今日重點」總結（1-2句話）
5. 不要加 markdown 標記，純文字即可
6. 如果原文是英文，請翻譯成繁體中文
"""

    try:
        payload = {
            "model": MINIMAX_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"以下是過去24小時收集到的新聞：\n{news_text}"},
            ],
            "temperature": 0.3,
            "max_completion_tokens": 4096,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            MINIMAX_API_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        # Strip <think>...</think> reasoning tags
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        if content:
            log(f"  MiniMax summarization OK ({len(content)} chars)")
            return content
    except Exception as e:
        log(f"  [WARN] MiniMax API failed: {e}")

    return _format_raw_list(entries)


def _format_raw_list(entries: list[dict]) -> str:
    """Fallback: format entries as a simple text list."""
    lines = []
    for i, entry in enumerate(entries[:10], 1):
        lines.append(f"【{entry['title']}】")
        if entry.get("summary"):
            lines.append(f"  {entry['summary'][:120]}")
        lines.append(f"  來源：{entry['source']}")
        if entry.get("link"):
            lines.append(f"  {entry['link']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File Output
# ---------------------------------------------------------------------------
def save_daily_report(topic_key: str, topic_name: str, digest: str,
                      entries: list[dict], data_dir: str) -> Path:
    """Save the daily digest and raw entries to a txt file."""
    today = datetime.now(TW).strftime("%Y-%m-%d")
    topic_dir = Path(data_dir) / topic_key
    topic_dir.mkdir(parents=True, exist_ok=True)

    file_path = topic_dir / f"{today}_{topic_key}.txt"

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(f"{'=' * 60}\n")
        f.write(f"  {topic_name} 每日趨勢報告\n")
        f.write(f"  日期：{today}\n")
        f.write(f"  收集新聞數：{len(entries)} 則\n")
        f.write(f"{'=' * 60}\n\n")

        f.write(digest)
        f.write("\n\n")

        f.write(f"{'=' * 60}\n")
        f.write(f"  原始新聞列表（共 {len(entries)} 則）\n")
        f.write(f"{'=' * 60}\n\n")

        for i, entry in enumerate(entries, 1):
            f.write(f"{i}. {entry['title']}\n")
            f.write(f"   來源：{entry['source']}")
            if entry.get("published"):
                f.write(f" | {entry['published']}")
            f.write("\n")
            if entry.get("link"):
                f.write(f"   {entry['link']}\n")
            if entry.get("summary"):
                f.write(f"   {entry['summary'][:200]}\n")
            f.write("\n")

    log(f"  Report saved: {file_path}")
    return file_path


# ---------------------------------------------------------------------------
# Telegram Push Notification
# ---------------------------------------------------------------------------
def send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not bot_token or not chat_id:
        log("  [SKIP] Telegram: no bot_token or chat_id configured")
        return False

    # Telegram has a 4096 char limit per message
    chunks = []
    if len(message) <= TELEGRAM_MSG_LIMIT:
        chunks.append(message)
    else:
        # Split at double newlines, keep chunks under limit
        parts = message.split("\n\n")
        current = ""
        for part in parts:
            if len(current) + len(part) + 2 > TELEGRAM_MSG_LIMIT - 100:
                if current:
                    chunks.append(current.strip())
                # If a single part exceeds the limit, force split it
                while len(part) > TELEGRAM_MSG_LIMIT - 100:
                    chunks.append(part[:TELEGRAM_MSG_LIMIT - 100])
                    part = part[TELEGRAM_MSG_LIMIT - 100:]
                current = part
            else:
                current += "\n\n" + part if current else part
        if current:
            chunks.append(current.strip())

    success = True
    for i, chunk in enumerate(chunks):
        try:
            url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib_request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if not result.get("ok"):
                    log(f"  [WARN] Telegram error: {result}")
                    success = False
        except Exception as e:
            log(f"  [WARN] Telegram send failed: {e}")
            success = False
        if i < len(chunks) - 1:
            time.sleep(0.5)  # Rate limiting

    if success:
        log(f"  Telegram sent ({len(chunks)} message(s))")
    return success


# ---------------------------------------------------------------------------
# MoneyPrinter Video Generation & Download
# ---------------------------------------------------------------------------
MONEYPRINTER_URL = os.environ.get("MONEYPRINTER_URL", "http://localhost:7860")
MP_SSH_HOST = os.environ.get("MP_HOST", "")
MP_SSH_USER = os.environ.get("MP_USER", "")
MP_SSH_PASS = os.environ.get("MP_PASS", "")
MP_VIDEO_DIR = "/home/$USER/apps/MoneyPrinterV2/.mp"

VIDEO_DOWNLOAD_MAP = {
    "kpop": {"folder": "Kpop Trend", "prefix": "Kpop Trend"},
    "anime": {"folder": "Daily Anime", "prefix": "Daily Anime"},
}


def trigger_video_generation(digest: str, topic_name: str, topic_key: str,
                             config: dict) -> bool:
    """Call MoneyPrinter API to generate a video and wait for completion."""
    mp_url = config.get("moneyprinter_url", MONEYPRINTER_URL)
    account_id = config.get("moneyprinter_account_id", "")

    if not account_id:
        log("  [SKIP] MoneyPrinter: no account_id configured")
        return False

    today = datetime.now(TW).strftime("%Y-%m-%d")
    source_text = (
        f"{'=' * 60}\n"
        f"  {topic_name} 每日趨勢報告\n"
        f"  日期：{today}\n"
        f"{'=' * 60}\n\n"
        f"{digest}"
    )

    try:
        form_data = {
            "account_id": account_id,
            "source_text": source_text,
            "content_mode": "daily_trend",
            "output_mode": "full_video",
            "custom_subject": f"{topic_name}每日趨勢",
            "custom_title": f"{topic_name} 每日趨勢 {today}",
            "custom_description": f"{today} {topic_name} 最新趨勢整理",
            "custom_script": "",
        }
        encoded = "&".join(
            f"{k}={urllib_request.quote(str(v), safe='')}"
            for k, v in form_data.items()
        )
        data = encoded.encode("utf-8")
        req = urllib_request.Request(
            f"{mp_url}/generate-custom",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=30) as resp:
            log(f"  MoneyPrinter triggered (HTTP {resp.status})")
    except Exception as e:
        log(f"  [WARN] MoneyPrinter trigger failed: {e}")
        return False

    # Poll until video is complete (max 25 minutes)
    log(f"  Waiting for video generation to complete...")
    poll_url = f"{mp_url}/api/job-state"
    max_wait = 2700  # 45 minutes
    waited = 0
    while waited < max_wait:
        time.sleep(15)
        waited += 15
        try:
            req = urllib_request.Request(poll_url)
            with urllib_request.urlopen(req, timeout=10) as resp:
                state = json.loads(resp.read().decode("utf-8"))
            job = state.get("job", {})
            status = job.get("status", "")
            progress = job.get("progress", 0)
            running = job.get("running", False)

            if waited % 60 == 0:
                log(f"  ... {status} | {job.get('stage', '')} | {progress}%")
            if status == "error":
                log(f"  [WARN] Video generation failed: {job.get('error', '')}")
                return False
            if not running and progress >= 90:
                log(f"  Video generation completed for {topic_name}!")
                return True
        except Exception:
            pass

    log(f"  [WARN] Video generation timed out for {topic_name}")
    return False


def download_video_from_mp(topic_key: str) -> bool:
    """Download the latest video from MoneyPrinter to the local HP3 folder."""
    mapping = VIDEO_DOWNLOAD_MAP.get(topic_key)
    if not mapping or not paramiko:
        return False

    today_short = datetime.now(TW).strftime("%y%m%d")
    local_folder = Path(SCRIPT_DIR) / "videos" / mapping["folder"]
    local_folder.mkdir(parents=True, exist_ok=True)
    local_file = local_folder / f"{mapping['prefix']} {today_short}.mp4"

    log(f"  Downloading video to {local_file}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(MP_SSH_HOST, username=MP_SSH_USER, password=MP_SSH_PASS, timeout=10)
    except Exception as e:
        log(f"  [WARN] Cannot connect to MoneyPrinter: {e}")
        return False

    try:
        video_path = ""
        # Try gui_last_result.json
        try:
            stdin, stdout, stderr = ssh.exec_command(
                f"cat {MP_VIDEO_DIR}/gui_last_result.json 2>/dev/null"
            )
            raw = stdout.read().decode("utf-8", errors="replace").strip()
            if raw:
                result = json.loads(raw)
                video_path = result.get("video_path", "")
        except Exception:
            pass

        # Fallback: latest .mp4
        if not video_path:
            stdin, stdout, stderr = ssh.exec_command(
                f"ls -t {MP_VIDEO_DIR}/*.mp4 2>/dev/null | head -1"
            )
            video_path = stdout.read().decode("utf-8").strip()

        if not video_path:
            log("  [WARN] No video file found on MoneyPrinter")
            return False

        sftp = ssh.open_sftp()
        stat = sftp.stat(video_path)
        size_mb = stat.st_size / 1024 / 1024
        log(f"  Remote: {video_path} ({size_mb:.1f} MB)")
        sftp.get(video_path, str(local_file))
        sftp.close()
        log(f"  Saved: {local_file}")
        return True
    except Exception as e:
        log(f"  [WARN] Download failed: {e}")
        return False
    finally:
        ssh.close()


# ---------------------------------------------------------------------------
# Main Process
# ---------------------------------------------------------------------------
def process_topic(topic_key: str, topic_config: dict, config: dict,
                  test_mode: bool = False) -> bool:
    """Process a single topic: collect, summarize, save, notify."""
    topic_name = topic_config.get("display_name", topic_key)
    log(f"Processing topic: {topic_name}")

    # 1. Collect feeds
    feeds = topic_config.get("feeds", [])
    if not feeds:
        log(f"  No feeds configured for {topic_key}")
        return False

    entries = collect_all_feeds(feeds, hours_back=28)
    log(f"  Total unique entries: {len(entries)}")

    if not entries:
        log(f"  No new entries found, skipping")
        return True

    # 2. Summarize with MiniMax
    digest = summarize_with_minimax(
        entries, topic_config, config.get("minimax_api_key", "")
    )

    # 3. Save daily report
    data_dir = config.get("data_dir", str(SCRIPT_DIR / "data"))
    report_path = save_daily_report(topic_key, topic_name, digest, entries, data_dir)

    # 4. Send Telegram notification
    if not test_mode:
        today = datetime.now(TW).strftime("%Y/%m/%d")
        tg_msg = f"{'=' * 25}\n{topic_name} 每日摘要 ({today})\n{'=' * 25}\n\n{digest}"
        send_telegram(
            config.get("telegram_bot_token", ""),
            config.get("telegram_chat_id", ""),
            tg_msg,
        )
    else:
        log("  [TEST MODE] Skipping Telegram notification")

    # 5. Trigger MoneyPrinter video generation (if configured)
    if topic_config.get("generate_video", False) and not test_mode:
        log(f"  Triggering video generation for {topic_name}...")
        video_ok = trigger_video_generation(digest, topic_name, topic_key, config)
        if video_ok:
            # 5a. Download video to HP3
            dl_ok = download_video_from_mp(topic_key)
            today_short = datetime.now(TW).strftime("%y%m%d")
            mapping = VIDEO_DOWNLOAD_MAP.get(topic_key, {})
            prefix = mapping.get("prefix", topic_name)
            msg = f"影片生成完成\n{prefix} {today_short}.mp4"
            if dl_ok:
                msg += "\n已下載到 HP3"
            send_telegram(
                config.get("telegram_bot_token", ""),
                config.get("telegram_chat_id", ""),
                msg,
            )
    elif topic_config.get("generate_video", False) and test_mode:
        log("  [TEST MODE] Skipping video generation")

    log(f"  Done: {topic_name}\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="Daily Trend Collector")
    parser.add_argument("--topic", type=str, help="Run specific topic only")
    parser.add_argument("--test", action="store_true", help="Test mode (no Telegram push)")
    args = parser.parse_args()

    log("=" * 50)
    log("Daily Trend Collector starting")
    log("=" * 50)

    config = load_config()
    topics = config.get("topics", {})

    if args.topic:
        if args.topic not in topics:
            log(f"[ERROR] Topic '{args.topic}' not found in config")
            sys.exit(1)
        topics = {args.topic: topics[args.topic]}

    for topic_key, topic_config in topics.items():
        if not topic_config.get("enabled", True):
            log(f"Skipping disabled topic: {topic_key}")
            continue
        try:
            process_topic(topic_key, topic_config, config, test_mode=args.test)
        except Exception as e:
            log(f"[ERROR] Topic {topic_key} failed: {e}")
            traceback.print_exc()

    log("All topics processed. Done!")


if __name__ == "__main__":
    main()
