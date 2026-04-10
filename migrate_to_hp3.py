#!/usr/bin/env python3
"""
Migration script: Move video generation scheduling from local PC to HP3.
Performs all steps: modify scripts, upload, create batch files, create tasks, cleanup.
"""

import io
import os
import sys
import time
import subprocess
import textwrap

# Fix encoding for SSH output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    import paramiko
except ImportError:
    print("[ERROR] paramiko not installed locally. Run: pip install paramiko")
    sys.exit(1)

HP3_HOST = os.environ.get("HP3_HOST", "")
HP3_USER = os.environ.get("HP3_USER", "")
HP3_PASS = os.environ.get("HP3_PASS", "")
HP3_PYTHON = r"C:\Program Files\Python312\python.exe"
HP3_DIR = r"C:\DailyTrend"

# ============================================================================
# Modified auto_report_video.py content (for HP3)
# ============================================================================
AUTO_REPORT_VIDEO_PY = r'''#!/usr/bin/env python3
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
def wait_for_completion(max_wait: int = 1500) -> bool:
    """Poll MoneyPrinter until video is done (max 25 minutes)."""
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
        # Get latest video path from gui_last_result.json
        stdin, stdout, stderr = ssh.exec_command(
            f"cat {MP_VIDEO_DIR}/../.mp/gui_last_result.json"
        )
        result = json.loads(stdout.read().decode("utf-8", errors="replace"))
        video_path = result.get("video_path", "")

        if not video_path:
            # Fallback: find latest .mp4
            stdin, stdout, stderr = ssh.exec_command(
                f"ls -t {MP_VIDEO_DIR}/*.mp4 | head -1"
            )
            video_path = stdout.read().decode("utf-8").strip()

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
'''

# ============================================================================
# Modified download_videos.py content (for HP3)
# ============================================================================
DOWNLOAD_VIDEOS_PY = r'''#!/usr/bin/env python3
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

# Output folders on HP3
VIDEO_DESTINATIONS = [
    {
        "topic": "kpop",
        "folder": r"C:\DailyTrend\videos\Kpop Trend",
        "filename_prefix": "Kpop Trend",
    },
    {
        "topic": "anime",
        "folder": r"C:\DailyTrend\videos\Daily Anime",
        "filename_prefix": "Daily Anime",
    },
]


def log(msg: str):
    ts = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def find_today_videos(sftp, target_date: str) -> list[dict]:
    """Find .mp4 files generated on the target date, sorted by mtime."""
    files = []
    for entry in sftp.listdir_attr(MP_VIDEO_DIR):
        if not entry.filename.endswith(".mp4"):
            continue
        mtime = datetime.fromtimestamp(entry.st_mtime, tz=TW)
        file_date = mtime.strftime("%Y-%m-%d")
        if file_date == target_date:
            files.append({
                "filename": entry.filename,
                "path": f"{MP_VIDEO_DIR}/{entry.filename}",
                "mtime": entry.st_mtime,
                "size": entry.st_size,
                "time_str": mtime.strftime("%H:%M:%S"),
            })

    # Sort by modification time (newest first)
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files


def download_videos(target_date: str):
    """Main download function."""
    date_short = target_date[2:].replace("-", "")  # "2026-04-09" -> "260409"

    log(f"Connecting to MoneyPrinter server {MP_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(MP_HOST, username=MP_USER, password=MP_PASS, timeout=10)
    except Exception as e:
        log(f"[ERROR] Cannot connect to {MP_HOST}: {e}")
        return False

    sftp = ssh.open_sftp()

    # Find today's videos
    videos = find_today_videos(sftp, target_date)
    log(f"Found {len(videos)} video(s) for {target_date}")

    if len(videos) < len(VIDEO_DESTINATIONS):
        log(f"[WARN] Expected {len(VIDEO_DESTINATIONS)} videos, found {len(videos)}")
        if not videos:
            sftp.close()
            ssh.close()
            return False

    # Take the latest N videos (newest first), then reverse so oldest=kpop, newest=anime
    latest = videos[:len(VIDEO_DESTINATIONS)]
    latest.reverse()  # Now: oldest (kpop) first, newest (anime) second

    downloaded = 0
    for idx, dest in enumerate(VIDEO_DESTINATIONS):
        if idx >= len(latest):
            log(f"  [SKIP] No video for {dest['topic']} (not generated yet)")
            continue

        video = latest[idx]
        local_folder = Path(dest["folder"])
        local_folder.mkdir(parents=True, exist_ok=True)
        local_file = local_folder / f"{dest['filename_prefix']} {date_short}.mp4"

        # Skip if already downloaded
        if local_file.exists():
            local_size = local_file.stat().st_size
            if local_size == video["size"]:
                log(f"  [SKIP] {dest['topic']}: already downloaded ({local_file.name})")
                downloaded += 1
                continue

        log(f"  Downloading {dest['topic']}... ({video['time_str']}, {video['size']/1024/1024:.1f} MB)")
        try:
            sftp.get(video["path"], str(local_file))
            log(f"  Saved: {local_file}")
            downloaded += 1
        except Exception as e:
            log(f"  [ERROR] Download failed for {dest['topic']}: {e}")

    sftp.close()
    ssh.close()

    log(f"Downloaded {downloaded}/{len(VIDEO_DESTINATIONS)} videos")
    return downloaded == len(VIDEO_DESTINATIONS)


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
'''

# ============================================================================
# Batch file contents
# ============================================================================
BATCH_AUTO_VIDEO_TECH = r'''@echo off
cd /d C:\DailyTrend
"C:\Program Files\Python312\python.exe" auto_report_video.py --type tech >> C:\DailyTrend\logs\auto_video_tech.log 2>&1
'''

BATCH_AUTO_VIDEO_NEWS = r'''@echo off
cd /d C:\DailyTrend
"C:\Program Files\Python312\python.exe" auto_report_video.py --type news >> C:\DailyTrend\logs\auto_video_news.log 2>&1
'''

BATCH_AUTO_VIDEO_MARKET = r'''@echo off
cd /d C:\DailyTrend
"C:\Program Files\Python312\python.exe" auto_report_video.py --type market >> C:\DailyTrend\logs\auto_video_market.log 2>&1
'''

BATCH_DOWNLOAD_VIDEOS = r'''@echo off
cd /d C:\DailyTrend
"C:\Program Files\Python312\python.exe" download_videos.py >> C:\DailyTrend\logs\download_videos.log 2>&1
'''


def ssh_exec(ssh, cmd, timeout=60):
    """Execute command via SSH and return output."""
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return out, err


def main():
    print("=" * 70)
    print("  MIGRATION: Move video scheduling to HP3 (remote host)")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # Step 1: Connect to HP3
    # -----------------------------------------------------------------------
    print("\n[STEP 1] Connecting to HP3...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HP3_HOST, username=HP3_USER, password=HP3_PASS, timeout=15)
        print("  Connected to HP3!")
    except Exception as e:
        print(f"  [ERROR] Cannot connect to HP3: {e}")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Step 2: Install paramiko on HP3
    # -----------------------------------------------------------------------
    print("\n[STEP 2] Installing paramiko on HP3...")
    out, err = ssh_exec(ssh, f'"{HP3_PYTHON}" -m pip install paramiko', timeout=120)
    print(f"  pip output: {out[-200:] if len(out) > 200 else out}")
    if err and "error" in err.lower():
        print(f"  pip stderr: {err[-200:]}")

    # -----------------------------------------------------------------------
    # Step 3: Create directories on HP3
    # -----------------------------------------------------------------------
    print("\n[STEP 3] Creating directories on HP3...")
    dirs_to_create = [
        r"C:\DailyTrend\logs",
        r"C:\DailyTrend\videos\Tech Report",
        r"C:\DailyTrend\videos\World News Report",
        r"C:\DailyTrend\videos\Market Report",
        r"C:\DailyTrend\videos\Kpop Trend",
        r"C:\DailyTrend\videos\Daily Anime",
    ]
    for d in dirs_to_create:
        out, err = ssh_exec(ssh, f'mkdir "{d}" 2>nul & echo done')
        print(f"  Created: {d}")

    # -----------------------------------------------------------------------
    # Step 4: Upload scripts via SFTP
    # -----------------------------------------------------------------------
    print("\n[STEP 4] Uploading scripts to HP3...")
    sftp = ssh.open_sftp()

    # Upload auto_report_video.py
    with sftp.open("C:/DailyTrend/auto_report_video.py", "w") as f:
        f.write(AUTO_REPORT_VIDEO_PY.encode("utf-8"))
    print("  Uploaded: auto_report_video.py")

    # Upload download_videos.py
    with sftp.open("C:/DailyTrend/download_videos.py", "w") as f:
        f.write(DOWNLOAD_VIDEOS_PY.encode("utf-8"))
    print("  Uploaded: download_videos.py")

    # -----------------------------------------------------------------------
    # Step 5: Create batch files on HP3
    # -----------------------------------------------------------------------
    print("\n[STEP 5] Creating batch files on HP3...")
    batch_files = {
        "C:/DailyTrend/run_auto_video_tech.bat": BATCH_AUTO_VIDEO_TECH,
        "C:/DailyTrend/run_auto_video_news.bat": BATCH_AUTO_VIDEO_NEWS,
        "C:/DailyTrend/run_auto_video_market.bat": BATCH_AUTO_VIDEO_MARKET,
        "C:/DailyTrend/run_download_videos.bat": BATCH_DOWNLOAD_VIDEOS,
    }
    for path, content in batch_files.items():
        with sftp.open(path, "w") as f:
            f.write(content.encode("utf-8"))
        print(f"  Created: {path}")

    sftp.close()

    # -----------------------------------------------------------------------
    # Step 6: Verify files on HP3
    # -----------------------------------------------------------------------
    print("\n[STEP 6] Verifying files on HP3...")
    out, err = ssh_exec(ssh, r'dir /b C:\DailyTrend\*.py C:\DailyTrend\*.bat')
    print(f"  Files: {out}")

    # Verify paramiko import works
    out, err = ssh_exec(ssh, f'"{HP3_PYTHON}" -c "import paramiko; print(paramiko.__version__)"')
    print(f"  paramiko version on HP3: {out}")
    if err:
        print(f"  [WARN] paramiko import error: {err}")

    # -----------------------------------------------------------------------
    # Step 7: Create Windows Task Scheduler entries on HP3
    # -----------------------------------------------------------------------
    print("\n[STEP 7] Creating scheduled tasks on HP3...")

    tasks = [
        {
            "name": "AutoVideo_Tech",
            "time": "10:00",
            "bat": r"C:\DailyTrend\run_auto_video_tech.bat",
        },
        {
            "name": "AutoVideo_News",
            "time": "13:00",
            "bat": r"C:\DailyTrend\run_auto_video_news.bat",
        },
        {
            "name": "DailyTrendDownload",
            "time": "15:00",
            "bat": r"C:\DailyTrend\run_download_videos.bat",
        },
        {
            "name": "AutoVideo_Market",
            "time": "19:30",
            "bat": r"C:\DailyTrend\run_auto_video_market.bat",
        },
    ]

    for task in tasks:
        # Delete existing task if any, then create new
        cmd_delete = f'schtasks /Delete /TN "{task["name"]}" /F 2>nul'
        ssh_exec(ssh, cmd_delete)

        cmd_create = (
            f'schtasks /Create /TN "{task["name"]}" '
            f'/TR "{task["bat"]}" '
            f'/SC DAILY /ST {task["time"]} '
            f'/RL HIGHEST /F'
        )
        out, err = ssh_exec(ssh, cmd_create)
        status = "OK" if "SUCCESS" in out.upper() or "success" in out.lower() else out + " " + err
        print(f"  {task['name']} ({task['time']}): {status}")

    # -----------------------------------------------------------------------
    # Step 8: Verify scheduled tasks on HP3
    # -----------------------------------------------------------------------
    print("\n[STEP 8] Verifying scheduled tasks on HP3...")
    # Query each task
    for task in tasks:
        out, err = ssh_exec(ssh, f'schtasks /Query /TN "{task["name"]}" /FO LIST')
        # Extract schedule info
        lines = out.split("\n")
        for line in lines:
            if "Next Run Time" in line or "Status" in line:
                print(f"  {task['name']}: {line.strip()}")

    # Also verify the existing DailyTrendCollector
    out, err = ssh_exec(ssh, 'schtasks /Query /TN "DailyTrendCollector" /FO LIST 2>nul')
    if out:
        for line in out.split("\n"):
            if "Next Run Time" in line or "Status" in line:
                print(f"  DailyTrendCollector: {line.strip()}")
    else:
        print("  [INFO] DailyTrendCollector not found - may have a different name")

    ssh.close()
    print("\n  HP3 setup complete!")

    # -----------------------------------------------------------------------
    # Step 9: Delete old scheduled tasks on THIS PC
    # -----------------------------------------------------------------------
    print("\n[STEP 9] Deleting old scheduled tasks on this PC...")
    old_tasks = [
        "DailyTrendDownload_1500",
        "DailyTrendDownload_1800",
        "AutoVideo_Tech_1000",
        "AutoVideo_News_1300",
        "AutoVideo_Market_1930",
    ]
    for task_name in old_tasks:
        try:
            result = subprocess.run(
                ["schtasks", "/Delete", "/TN", task_name, "/F"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print(f"  Deleted: {task_name}")
            else:
                stderr_msg = result.stderr.strip()
                if "does not exist" in stderr_msg.lower() or "cannot find" in stderr_msg.lower():
                    print(f"  [SKIP] {task_name}: not found (already removed)")
                else:
                    print(f"  [WARN] {task_name}: {stderr_msg}")
        except Exception as e:
            print(f"  [ERROR] {task_name}: {e}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  MIGRATION COMPLETE!")
    print("=" * 70)
    print("""
  Schedule on HP3 (REMOTE_HOST):
    09:00  DailyTrendCollector  - KPOP/Anime news (already existed)
    10:00  AutoVideo_Tech       - Tech report video
    13:00  AutoVideo_News       - News report video
    15:00  DailyTrendDownload   - Download KPOP/Anime videos
    19:30  AutoVideo_Market     - Market report video

  Files on HP3:
    C:\\DailyTrend\\auto_report_video.py
    C:\\DailyTrend\\download_videos.py
    C:\\DailyTrend\\run_auto_video_tech.bat
    C:\\DailyTrend\\run_auto_video_news.bat
    C:\\DailyTrend\\run_auto_video_market.bat
    C:\\DailyTrend\\run_download_videos.bat

  Videos will be saved to:
    C:\\DailyTrend\\videos\\Tech Report\\
    C:\\DailyTrend\\videos\\World News Report\\
    C:\\DailyTrend\\videos\\Market Report\\
    C:\\DailyTrend\\videos\\Kpop Trend\\
    C:\\DailyTrend\\videos\\Daily Anime\\

  Old tasks on this PC: DELETED
""")


if __name__ == "__main__":
    main()
