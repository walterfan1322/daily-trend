#!/usr/bin/env python3
"""
Auto Report Video Generator (HP3 version)
==========================================
Fetches today's report from Mac Mini, sends to MoneyPrinter for video generation,
waits for completion, then downloads the video locally on HP3.

Usage:
    python auto_report_video.py --type tech
    python auto_report_video.py --type news
    python auto_report_video.py --type market
    python auto_report_video.py --type all     # Run all types sequentially
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as urllib_request
from urllib import error as urllib_error

try:
    import paramiko
except ImportError:
    print("[ERROR] paramiko not installed. Run: pip install paramiko")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TW = timezone(timedelta(hours=8))

# Mac Mini (source of report files)
MAC_HOST = os.environ.get("MAC_HOST", "")
MAC_USER = os.environ.get("MAC_USER", "")
MAC_PASS = os.environ.get("MAC_PASS", "")

# MoneyPrinter (video generation)
MP_HOST = os.environ.get("MP_HOST", "")
MP_USER = os.environ.get("MP_USER", "")
MP_PASS = os.environ.get("MP_PASS", "")
MP_URL = os.environ.get("MONEYPRINTER_URL", "http://localhost:7860")
MP_ACCOUNT_ID = os.environ.get("MP_ACCOUNT_ID", "")
MP_VIDEO_DIR = "/home/$USER/apps/MoneyPrinterV2/.mp"

# Telegram notification
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# Report type configurations - paths are local to HP3
REPORT_TYPES = {
    "tech": {
        "mac_folder": "tech trend",
        "file_pattern": "{date}_\u79d1\u6280\u60c5\u5831.txt",
        "content_mode": "daily_tech_news",
        "subject": "\u4eca\u65e5\u79d1\u6280\u65b0\u805e\u91cd\u9ede",
        "title_tpl": "\u79d1\u6280\u60c5\u5831 {date_short}",
        "description_tpl": "{date} \u79d1\u6280\u65b0\u805e\u91cd\u9ede\u6574\u7406",
        "local_folder": r"C:\DailyTrend\videos\Tech Report",
        "local_prefix": "Tech News Report",
    },
    "news": {
        "mac_folder": "news trend",
        "file_pattern": "{date}_\u570b\u969b\u65b0\u805e.txt",
        "content_mode": "international_brief",
        "subject": "\u570b\u969b\u60c5\u52e2\u5831\u544a",
        "title_tpl": "\u570b\u969b\u65b0\u805e {date_short}",
        "description_tpl": "{date} \u570b\u969b\u65b0\u805e\u91cd\u9ede\u6574\u7406",
        "local_folder": r"C:\DailyTrend\videos\World News Report",
        "local_prefix": "World News Report",
    },
    "market": {
        "mac_folder": "market trend",
        "file_pattern": "{date}.txt",
        "content_mode": "market_report",
        "subject": "\u4eca\u65e5\u5e02\u5834\u4e3b\u984c\u5831\u544a",
        "title_tpl": "\u5e02\u5834\u4e3b\u984c\u5831\u544a {date_short}",
        "description_tpl": "{date} \u5e02\u5834\u4e3b\u984c\u5831\u544a",
        "local_folder": r"C:\DailyTrend\videos\Market Report",
        "local_prefix": "\u5e02\u5834\u4e3b\u984c\u5831\u544a",
    },
}


def log(msg: str):
    ts = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


# ---------------------------------------------------------------------------
# Step 1: Fetch report from Mac Mini
# ---------------------------------------------------------------------------
def fetch_report_from_mac(report_type: str, target_date: str) -> str:
    """Connect to Mac Mini and read today's report file."""
    cfg = REPORT_TYPES[report_type]
    filename = cfg["file_pattern"].format(date=target_date)
    remote_path = f"/Users/$USER/Desktop/{cfg['mac_folder']}/{filename}"

    log(f"Fetching report from Mac Mini: {remote_path}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(MAC_HOST, username=MAC_USER, password=MAC_PASS, timeout=10)
    except Exception as e:
        log(f"[ERROR] Cannot connect to Mac Mini: {e}")
        return ""

    try:
        sftp = ssh.open_sftp()
        with sftp.open(remote_path, "r") as f:
            content = f.read().decode("utf-8", errors="replace")
        sftp.close()
        log(f"  Report loaded: {len(content)} chars")
        return content
    except FileNotFoundError:
        log(f"[ERROR] Report file not found: {remote_path}")
        return ""
    except Exception as e:
        log(f"[ERROR] Failed to read report: {e}")
        return ""
    finally:
        ssh.close()


# ---------------------------------------------------------------------------
# Step 2: Trigger MoneyPrinter video generation
# ---------------------------------------------------------------------------
def trigger_moneyprinter(report_type: str, source_text: str, target_date: str) -> bool:
    """Send report to MoneyPrinter and start video generation."""
    cfg = REPORT_TYPES[report_type]
    date_short = target_date[2:].replace("-", "")  # "260409"

    form_data = {
        "account_id": MP_ACCOUNT_ID,
        "source_text": source_text,
        "content_mode": cfg["content_mode"],
        "output_mode": "full_video",
        "custom_subject": cfg["subject"],
        "custom_title": cfg["title_tpl"].format(date_short=date_short),
        "custom_description": cfg["description_tpl"].format(date=target_date),
        "custom_script": "",
    }

    encoded = "&".join(
        f"{k}={urllib_request.quote(str(v), safe='')}"
        for k, v in form_data.items()
    )
    data = encoded.encode("utf-8")

    log(f"Triggering MoneyPrinter ({cfg['content_mode']})...")
    try:
        req = urllib_request.Request(
            f"{MP_URL}/generate-custom",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=30) as resp:
            log(f"  MoneyPrinter triggered (HTTP {resp.status})")
            return True
    except Exception as e:
        log(f"[ERROR] MoneyPrinter trigger failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Step 3: Wait for video generation to complete
# ---------------------------------------------------------------------------
def wait_for_completion(max_wait: int = 2700) -> bool:
    """Poll MoneyPrinter until video is done (max 45 minutes)."""
    log("Waiting for video generation...")
    poll_url = f"{MP_URL}/api/job-state"
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
            stage = job.get("stage", "")
            running = job.get("running", False)

            if waited % 60 == 0:
                log(f"  ... {status} | {stage} | {progress}%")

            if status == "error":
                log(f"[ERROR] Video generation failed: {job.get('error', '')}")
                return False
            if not running and progress >= 90:
                log("Video generation completed!")
                return True
        except Exception:
            pass

    log("[ERROR] Video generation timed out")
    return False


# ---------------------------------------------------------------------------
# Step 4: Download the generated video
# ---------------------------------------------------------------------------
def download_video(report_type: str, target_date: str) -> bool:
    """Download the latest video from MoneyPrinter to the local folder."""
    cfg = REPORT_TYPES[report_type]
    date_short = target_date[2:].replace("-", "")  # "260409"

    local_folder = Path(cfg["local_folder"])
    local_folder.mkdir(parents=True, exist_ok=True)
    local_file = local_folder / f"{cfg['local_prefix']} {date_short}.mp4"

    log(f"Downloading video to {local_file}...")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(MP_HOST, username=MP_USER, password=MP_PASS, timeout=10)
    except Exception as e:
        log(f"[ERROR] Cannot connect to MoneyPrinter server: {e}")
        return False

    try:
        video_path = ""
        # Try gui_last_result.json first
        try:
            stdin, stdout, stderr = ssh.exec_command(
                f"cat {MP_VIDEO_DIR}/gui_last_result.json 2>/dev/null"
            )
            raw = stdout.read().decode("utf-8", errors="replace").strip()
            if raw:
                result = json.loads(raw)
                video_path = result.get("video_path", "")
                if video_path:
                    log(f"  Found via result file: {video_path}")
        except (json.JSONDecodeError, Exception) as e:
            log(f"  Result file unavailable ({e}), using fallback...")

        if not video_path:
            # Fallback: find latest .mp4 by modification time
            stdin, stdout, stderr = ssh.exec_command(
                f"ls -t {MP_VIDEO_DIR}/*.mp4 2>/dev/null | head -1"
            )
            video_path = stdout.read().decode("utf-8").strip()
            if video_path:
                log(f"  Found via latest file: {video_path}")

        if not video_path:
            log("[ERROR] No video file found")
            return False

        sftp = ssh.open_sftp()
        stat = sftp.stat(video_path)
        log(f"  Remote: {video_path} ({stat.st_size/1024/1024:.1f} MB)")

        sftp.get(video_path, str(local_file))
        sftp.close()

        log(f"  Saved: {local_file}")
        return True
    except Exception as e:
        log(f"[ERROR] Download failed: {e}")
        return False
    finally:
        ssh.close()


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------
def notify_telegram(message: str):
    """Send a Telegram notification."""
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TG_CHAT_ID, "text": message}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib_request.urlopen(req, timeout=10) as resp:
            pass
        log(f"  Telegram notified")
    except Exception as e:
        log(f"  [WARN] Telegram notification failed: {e}")


# ---------------------------------------------------------------------------
# Step 5: Check if MoneyPrinter is busy
# ---------------------------------------------------------------------------
def wait_until_free(max_wait: int = 1800) -> bool:
    """Wait until MoneyPrinter is not running any job."""
    log("Checking if MoneyPrinter is free...")
    waited = 0
    while waited < max_wait:
        try:
            req = urllib_request.Request(f"{MP_URL}/api/job-state")
            with urllib_request.urlopen(req, timeout=10) as resp:
                state = json.loads(resp.read().decode("utf-8"))
            job = state.get("job", {})
            if not job.get("running", False):
                log("  MoneyPrinter is free!")
                return True
            stage = job.get("stage", "")
            progress = job.get("progress", 0)
            log(f"  Busy: {stage} ({progress}%). Waiting...")
        except Exception:
            pass
        time.sleep(30)
        waited += 30

    log("[ERROR] MoneyPrinter still busy after max wait")
    return False


# ---------------------------------------------------------------------------
# Main pipeline for a single report type
# ---------------------------------------------------------------------------
def process_report(report_type: str, target_date: str) -> bool:
    """Full pipeline: fetch report -> generate video -> download."""
    log(f"Processing: {report_type}")
    log("=" * 50)

    # 1. Fetch report from Mac Mini
    source_text = fetch_report_from_mac(report_type, target_date)
    if not source_text:
        log(f"[FAIL] No report for {report_type} on {target_date}")
        return False

    # 2. Wait until MoneyPrinter is free
    if not wait_until_free():
        return False

    # 3. Trigger video generation
    if not trigger_moneyprinter(report_type, source_text, target_date):
        return False

    # 4. Wait for completion
    if not wait_for_completion():
        return False

    # 5. Download the video locally (already on HP3)
    if not download_video(report_type, target_date):
        return False

    # 6. Send Telegram notification
    cfg = REPORT_TYPES[report_type]
    date_short = target_date[2:].replace("-", "")
    notify_telegram(f"影片生成完成\n{cfg['local_prefix']} {date_short}.mp4")

    log(f"[OK] {report_type} video complete!\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="Auto Report Video Generator")
    parser.add_argument("--type", required=True, choices=["tech", "news", "market", "all"],
                        help="Report type to process")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD), default=today")
    args = parser.parse_args()

    target_date = args.date or datetime.now(TW).strftime("%Y-%m-%d")

    log("=" * 60)
    log(f"Auto Report Video Generator")
    log(f"Date: {target_date} | Type: {args.type}")
    log("=" * 60)

    if args.type == "all":
        types = ["tech", "news", "market"]
    else:
        types = [args.type]

    results = {}
    for rtype in types:
        results[rtype] = process_report(rtype, target_date)

    log("\n" + "=" * 60)
    log("Summary:")
    for rtype, success in results.items():
        status = "OK" if success else "FAIL"
        log(f"  {rtype}: {status}")
    log("=" * 60)

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
