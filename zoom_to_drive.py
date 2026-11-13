"""
Copy Zoom cloud recordings into Google Drive, one folder per batch,
with a sub-folder per class day.

Runs every morning (see .github/workflows/zoom-to-drive.yml).
Safe to re-run: a file that is already in the day folder is skipped.
"""

import json
import os
import re
import sys
import tempfile
import datetime as dt
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

IST = ZoneInfo("Asia/Kolkata")
FOLDER_MIME = "application/vnd.google-apps.folder"

# If Zoom saved several video layouts, pick the first one found in this order.
PREFERRED_LAYOUTS = [
    "shared_screen_with_speaker_view",
    "shared_screen_with_speaker_view(CC)",
    "shared_screen_with_gallery_view",
    "shared_screen",
    "active_speaker",
    "gallery_view",
]

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- config

def load_config():
    with open(os.path.join(os.path.dirname(__file__), "config.json")) as f:
        cfg = json.load(f)
    by_id = {}
    for b in cfg["batches"]:
        mid = str(b["meeting_id"]).replace(" ", "")
        by_id[mid] = b
    cfg["by_meeting_id"] = by_id
    return cfg


# ---------------------------------------------------------------- Zoom

def zoom_token():
    r = requests.post(
        "https://zoom.us/oauth/token",
        params={"grant_type": "account_credentials",
                "account_id": os.environ["ZOOM_ACCOUNT_ID"]},
        auth=(os.environ["ZOOM_CLIENT_ID"], os.environ["ZOOM_CLIENT_SECRET"]),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def list_recordings(token, user, date_from, date_to):
    url = f"https://api.zoom.us/v2/users/{quote(user)}/recordings"
    params = {"from": date_from, "to": date_to, "page_size": 300}
    meetings = []
    while True:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                         params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        meetings += data.get("meetings", [])
        nxt = data.get("next_page_token")
        if not nxt:
            return meetings
        params["next_page_token"] = nxt


def recordings_for_batches(token, cfg, date_from, date_to):
    """Each batch can have a different Zoom host (e.g. Vijay hosts the AI batch).
    List recordings once per host, then keep only the batch meetings."""
    hosts = sorted({b["zoom_host"] for b in cfg["batches"]})
    meetings = []
    for host in hosts:
        meetings += list_recordings(token, host, date_from, date_to)
    return [m for m in meetings if str(m["id"]) in cfg["by_meeting_id"]]


def pick_video(meeting):
    files = [f for f in meeting.get("recording_files", [])
             if f.get("file_type") == "MP4" and f.get("status") == "completed"]

    def rank(f):
        t = f.get("recording_type", "")
        return PREFERRED_LAYOUTS.index(t) if t in PREFERRED_LAYOUTS else 99

    files.sort(key=rank)
    return files[0] if files else None


def download(token, url, path):
    with requests.get(url, params={"access_token": token},
                      stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(path, "wb") as out:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                out.write(chunk)


def delete_from_zoom(token, meeting_uuid):
    # Zoom needs the UUID double-encoded if it starts with "/" or contains "//"
    enc = quote(meeting_uuid, safe="")
    if meeting_uuid.startswith("/") or "//" in meeting_uuid:
        enc = quote(enc, safe="")
    r = requests.delete(
        f"https://api.zoom.us/v2/meetings/{enc}/recordings",
        headers={"Authorization": f"Bearer {token}"},
        params={"action": "trash"},  # goes to Zoom trash, recoverable for 30 days
        timeout=60,
    )
    r.raise_for_status()


# ---------------------------------------------------------------- Google Drive

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.send",  # for the weekly summary email
]


def google_creds():
    return Credentials(
        None,
        refresh_token=os.environ["GDRIVE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GDRIVE_CLIENT_ID"],
        client_secret=os.environ["GDRIVE_CLIENT_SECRET"],
        scopes=GOOGLE_SCOPES,
    )


def drive_service():
    return build("drive", "v3", credentials=google_creds(), cache_discovery=False)


def q_escape(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def list_children(svc, parent_id, extra_query=""):
    q = f"'{parent_id}' in parents and trashed = false" + extra_query
    items, token = [], None
    while True:
        resp = svc.files().list(
            q=q, fields="nextPageToken, files(id, name, mimeType, size)",
            pageSize=1000, pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        items += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            return items


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
DAY_NUM_RE = re.compile(r"^\s*Day\s*(\d+)", re.I)
DATE_SLASH_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")                 # 12/11/2026, 7/11/2026
DATE_WORDS_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})")  # 6th November 2026


def folder_date(name):
    """Read the class date out of a day-folder name, if it has one."""
    m = DATE_SLASH_RE.search(name)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return dt.date(y, mo, d)
        except ValueError:
            return None
    m = DATE_WORDS_RE.search(name)
    if m:
        mo = MONTHS.get(m.group(2).lower()) or next(
            (v for k, v in MONTHS.items() if k.startswith(m.group(2).lower()[:3])), None)
        if mo:
            try:
                return dt.date(int(m.group(3)), mo, int(m.group(1)))
            except ValueError:
                return None
    return None


def find_day_folder(folders, class_day):
    for f in folders:
        if folder_date(f["name"]) == class_day:
            return f
    return None


def get_or_create_day_folder(svc, parent_id, class_day):
    """Use the existing day folder for this date, or create the next one.

    New folders follow your current pattern: "Day15 - 13/11/2026",
    numbered one after the highest DayN already in the batch folder.
    """
    folders = list_children(svc, parent_id, f" and mimeType = '{FOLDER_MIME}'")
    found = find_day_folder(folders, class_day)
    if found:
        return found["id"], found["name"]

    numbers = [int(m.group(1)) for f in folders if (m := DAY_NUM_RE.match(f["name"]))]
    next_num = (max(numbers) if numbers else 0) + 1
    name = f"Day{next_num} - {class_day:%d/%m/%Y}"
    if DRY_RUN:
        return None, name + " (would be created)"
    created = svc.files().create(
        body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        fields="id", supportsAllDrives=True,
    ).execute()
    return created["id"], name


def zoom_file_prefix(video):
    """Zoom names its downloads GMT20261112-110039_Recording_1920x1080.mp4.
    The GMT part is the recording start time, so it identifies the file."""
    t = dt.datetime.fromisoformat(video["recording_start"].replace("Z", "+00:00"))
    return f"GMT{t:%Y%m%d-%H%M%S}"


def already_uploaded(svc, folder_id, prefix):
    """True if the day folder already has this recording - whether the
    script put it there or you uploaded it by hand."""
    if folder_id is None:
        return False
    hits = list_children(svc, folder_id, f" and name contains '{q_escape(prefix)}'")
    return any(h["name"].startswith(prefix) for h in hits)


def upload(svc, folder_id, local_path, file_name):
    media = MediaFileUpload(local_path, mimetype="video/mp4",
                            resumable=True, chunksize=32 * 1024 * 1024)
    req = svc.files().create(
        body={"name": file_name, "parents": [folder_id]},
        media_body=media, fields="id", supportsAllDrives=True,
    )
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log(f"      uploaded {int(status.progress() * 100)}%")
    return resp["id"]


# ---------------------------------------------------------------- main

def main():
    cfg = load_config()
    lookback = int(cfg.get("lookback_days", 2))
    start_from = dt.date.fromisoformat(cfg.get("start_from_date", "2000-01-01"))
    delete_after = bool(cfg.get("delete_from_zoom_after_upload", False))
    min_minutes = int(cfg.get("skip_recordings_shorter_than_minutes", 10))

    today = dt.datetime.now(IST).date()
    date_from = (today - dt.timedelta(days=lookback)).isoformat()
    date_to = today.isoformat()
    log(f"Checking Zoom recordings from {date_from} to {date_to}"
        + ("  [DRY RUN]" if DRY_RUN else ""))

    token = zoom_token()
    svc = drive_service()
    meetings = recordings_for_batches(token, cfg, date_from, date_to)
    meetings.sort(key=lambda m: m["start_time"])  # oldest first, keeps day numbers in order

    errors = 0
    copied = 0
    for m in meetings:
        batch = cfg["by_meeting_id"].get(str(m["id"]))
        if not batch:
            continue  # not one of the class meetings

        start = dt.datetime.fromisoformat(m["start_time"].replace("Z", "+00:00")).astimezone(IST)
        log(f"\n{batch['name']} | {start:%d %b %Y %I:%M %p} | {m.get('topic', '')}")

        if m.get("duration", 999) < min_minutes:
            log(f"   only {m.get('duration')} min long - skipped as a false start")
            continue

        if start.date() < start_from:
            log(f"   before start date {start_from} - left alone")
            continue

        video = pick_video(m)
        if not video:
            log("   no finished MP4 yet - will try again tomorrow")
            continue

        prefix = zoom_file_prefix(video)
        file_name = f"{prefix}_Recording.mp4"
        try:
            folder_id, folder_name = get_or_create_day_folder(
                svc, batch["drive_folder_id"], start.date())
            log(f"   day folder: {folder_name}")

            if already_uploaded(svc, folder_id, prefix):
                log(f"   already in Drive: {file_name}")
                continue

            size_mb = video.get("file_size", 0) / (1024 * 1024)
            if DRY_RUN:
                log(f"   would copy: {file_name} ({size_mb:.0f} MB)")
                continue

            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "recording.mp4")
                log(f"   downloading {size_mb:.0f} MB from Zoom")
                download(token, video["download_url"], path)
                log(f"   uploading to Drive as {file_name}")
                upload(svc, folder_id, path, file_name)
            copied += 1

            if delete_after:
                delete_from_zoom(token, m["uuid"])
                log("   moved Zoom cloud copy to Zoom trash")
        except Exception as e:  # keep going with the other recordings
            errors += 1
            log(f"   ERROR: {e}")

    log(f"\nDone. Copied {copied} recording(s), {errors} error(s).")
    if errors:
        sys.exit(1)  # makes the GitHub run show as failed, so you get an email


if __name__ == "__main__":
    main()
