#!/usr/bin/env python3
"""
Download Videos from MoneyPrinter (HP3 version)
=================================================
Downloads today's generated trend videos from MoneyPrinter (remote host)
to local folders on HP3.

Scheduled to run at 15:00 daily on HP3.

Usage:
    python download_videos.py          # Download today's videos
    python download_videos.py --date 2026-04-09   # Download specific date
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as urllib_request

try:
    import paramiko
except ImportError:
    print("[ERROR] paramiko not installed. Run: pip install paramiko")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TW = timezone(timedelta(hours=8))

# MoneyPrinter server
MP_HOST = os.environ.get("MP_HOST", "")
MP_USER = os.environ.get("MP_USER", "")
MP_PASS = os.environ.get("MP_PASS", "")
MP_VIDEO_DIR = "/home/$USER/apps/MoneyPrinterV2/.mp"

# Output folders on HP3 — keyed by registry subject keyword
VIDEO_MAPPING = {
    "KPOP": {
        "folder": r"C:\DailyTrend\videos\Kpop Trend",
        "filename_prefix": "Kpop Trend",
        "keywords": ["KPOP", "韓流", "韓團"],
    },
    "Anime": {
        "folder": r"C:\DailyTrend\videos\Daily Anime",
        "filename_prefix": "Daily Anime",
        "keywords": ["動漫", "動畫", "Anime"],
    },
}


def log(msg: str):
    ts = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def download_videos(target_date: str):
    """Main download function — uses file registry to identify videos."""
    date_short = target_date[2:].replace("-", "")

    log(f"Connecting to MoneyPrinter server {MP_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(MP_HOST, username=MP_USER, password=MP_PASS, timeout=10)
    except Exception as e:
        log(f"[ERROR] Cannot connect to {MP_HOST}: {e}")
        return False

    sftp = ssh.open_sftp()

    # Load file registry to identify videos by subject
    registry = {}
    try:
        reg_path = f"{MP_VIDEO_DIR}/_file_registry.json"
        with sftp.open(reg_path, "r") as f:
            registry = json.loads(f.read().decode("utf-8"))
        log(f"  Loaded file registry ({len(registry)} entries)")
    except Exception as e:
        log(f"  [WARN] Could not load registry: {e}")

    # Find today's mp4 files
    today_files = {}
    for entry in sftp.listdir_attr(MP_VIDEO_DIR):
        if not entry.filename.endswith(".mp4"):
            continue
        mtime = datetime.fromtimestamp(entry.st_mtime, tz=TW)
        if mtime.strftime("%Y-%m-%d") == target_date:
            today_files[entry.filename] = {
                "path": f"{MP_VIDEO_DIR}/{entry.filename}",
                "mtime": entry.st_mtime,
                "size": entry.st_size,
                "time_str": mtime.strftime("%H:%M:%S"),
            }

    log(f"  Found {len(today_files)} video(s) for {target_date}")

    # Match each video to a topic using registry keywords
    downloaded = 0
    for topic_key, dest in VIDEO_MAPPING.items():
        # Find the best matching video from registry
        best_file = None
        best_mtime = 0
        for filename, meta in registry.items():
            if filename not in today_files:
                continue
            subject = meta.get("subject", "") + " " + meta.get("title", "")
            if any(kw in subject for kw in dest["keywords"]):
                if today_files[filename]["mtime"] > best_mtime:
                    best_mtime = today_files[filename]["mtime"]
                    best_file = filename

        if not best_file:
            log(f"  [SKIP] {topic_key}: no matching video in registry")
            continue

        video = today_files[best_file]
        local_folder = Path(dest["folder"])
        local_folder.mkdir(parents=True, exist_ok=True)
        local_file = local_folder / f"{dest['filename_prefix']} {date_short}.mp4"

        # Skip if already downloaded with same size
        if local_file.exists() and local_file.stat().st_size == video["size"]:
            log(f"  [SKIP] {topic_key}: already downloaded ({local_file.name})")
            downloaded += 1
            continue

        log(f"  Downloading {topic_key}... ({video['time_str']}, {video['size']/1024/1024:.1f} MB)")
        try:
            sftp.get(video["path"], str(local_file))
            log(f"  Saved: {local_file}")
            downloaded += 1
        except Exception as e:
            log(f"  [ERROR] Download failed for {topic_key}: {e}")

    sftp.close()
    ssh.close()

    log(f"Downloaded {downloaded}/{len(VIDEO_MAPPING)} videos")
    return downloaded == len(VIDEO_MAPPING)


def main():
    parser = argparse.ArgumentParser(description="Download trend videos from MoneyPrinter")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD), default=today")
    args = parser.parse_args()

    target_date = args.date or datetime.now(TW).strftime("%Y-%m-%d")

    log("=" * 50)
    log(f"Video Download - target date: {target_date}")
    log("=" * 50)

    success = download_videos(target_date)

    if success:
        log("All videos downloaded successfully!")
    else:
        log("Some videos could not be downloaded. Will retry later if scheduled.")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
