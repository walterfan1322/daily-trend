#!/usr/bin/env python3
"""
Sync Videos from HP3
====================
Downloads all new videos from HP3 to E:\tiktok_video\ on this machine.
Skips files that already exist with the same size.

Usage:
    python sync_videos.py              # Sync all
    python sync_videos.py --date 2026-04-09  # Sync specific date only
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("[ERROR] paramiko not installed. Run: pip install paramiko")
    sys.exit(1)

TW = timezone(timedelta(hours=8))

HP3_HOST = os.environ.get("HP3_HOST", "")
HP3_USER = os.environ.get("HP3_USER", "")
HP3_PASS = os.environ.get("HP3_PASS", "")
HP3_VIDEO_BASE = "C:/DailyTrend/videos"

# HP3 folder name → local E: drive folder
FOLDER_MAP = {
    "Kpop Trend":        r"E:\tiktok_video\Kpop Trend",
    "Daily Anime":       r"E:\tiktok_video\Daily Anime",
    "Tech Report":       r"E:\tiktok_video\Tech Report",
    "World News Report": r"E:\tiktok_video\World News Report",
    "Market Report":     r"E:\tiktok_video\Market Report",
}


def log(msg: str):
    ts = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def sync_videos(date_filter: str = ""):
    log("Connecting to HP3...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HP3_HOST, username=HP3_USER, password=HP3_PASS, timeout=10)
    except Exception as e:
        log(f"[ERROR] Cannot connect to HP3: {e}")
        return False

    sftp = ssh.open_sftp()
    total_new = 0
    total_skip = 0

    for hp3_folder, local_folder in FOLDER_MAP.items():
        remote_dir = f"{HP3_VIDEO_BASE}/{hp3_folder}"
        local_dir = Path(local_folder)
        local_dir.mkdir(parents=True, exist_ok=True)

        try:
            files = sftp.listdir_attr(remote_dir)
        except FileNotFoundError:
            continue

        for entry in files:
            if not entry.filename.endswith(".mp4"):
                continue
            # Date filter
            if date_filter and date_filter.replace("-", "")[2:] not in entry.filename:
                continue

            remote_path = f"{remote_dir}/{entry.filename}"
            local_path = local_dir / entry.filename

            # Skip if same size exists
            if local_path.exists() and local_path.stat().st_size == entry.st_size:
                total_skip += 1
                continue

            size_mb = entry.st_size / 1024 / 1024
            log(f"  Downloading: {hp3_folder}/{entry.filename} ({size_mb:.1f} MB)")
            try:
                sftp.get(remote_path, str(local_path))
                total_new += 1
            except Exception as e:
                log(f"  [ERROR] {e}")

    sftp.close()
    ssh.close()

    log(f"Done! {total_new} new, {total_skip} skipped (already exists)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Sync videos from HP3")
    parser.add_argument("--date", type=str, help="Only sync specific date (YYYY-MM-DD)")
    args = parser.parse_args()

    log("=" * 40)
    log("Video Sync from HP3")
    log("=" * 40)
    sync_videos(args.date or "")


if __name__ == "__main__":
    main()
