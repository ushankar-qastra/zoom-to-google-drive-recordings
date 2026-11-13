"""
Weekly summary email: what was copied to Drive last week, per batch,
and any Zoom recording that is missing from Drive.

Runs every Monday morning (see .github/workflows/weekly-summary.yml).
"""

import base64
import datetime as dt
import os
from email.mime.text import MIMEText

from googleapiclient.discovery import build

import zoom_to_drive as z


def main():
    cfg = z.load_config()
    to = cfg.get("summary_email_to")
    today = dt.datetime.now(z.IST).date()
    week_start = today - dt.timedelta(days=7)   # last Monday
    week_end = today - dt.timedelta(days=1)     # yesterday (Sunday)

    creds = z.google_creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    token = z.zoom_token()
    meetings = z.recordings_for_batches(token, cfg, week_start.isoformat(), week_end.isoformat())
    min_minutes = int(cfg.get("skip_recordings_shorter_than_minutes", 10))

    lines = [f"Class recordings in Drive, {week_start:%a %d %b} to {week_end:%a %d %b %Y}", ""]
    issues = 0

    for batch in cfg["batches"]:
        lines.append(batch["name"])
        folders = z.list_children(drive, batch["drive_folder_id"],
                                  f" and mimeType = '{z.FOLDER_MIME}'")

        # 1. Day folders for last week and what's in them
        week = []
        for f in folders:
            d = z.folder_date(f["name"])
            if d and week_start <= d <= week_end:
                week.append((d, f))
        week.sort(key=lambda x: x[0])

        if not week:
            lines.append("   No classes recorded this week")
        for d, f in week:
            files = z.list_children(drive, f["id"])
            videos = [x for x in files
                      if x.get("mimeType", "").startswith("video/")
                      or x["name"].lower().endswith(".mp4")]
            if videos:
                mb = sum(int(v.get("size", 0)) for v in videos) / (1024 * 1024)
                n = len(videos)
                lines.append(f"   {f['name']}  ({d:%a})  - {n} video{'s' if n > 1 else ''}, {mb:.0f} MB")
            else:
                issues += 1
                lines.append(f"   {f['name']}  ({d:%a})  - EMPTY, no video in folder")

        # 2. Zoom recordings from last week that aren't in Drive
        for m in meetings:
            if str(m["id"]) != str(batch["meeting_id"]).replace(" ", ""):
                continue
            start = dt.datetime.fromisoformat(
                m["start_time"].replace("Z", "+00:00")).astimezone(z.IST)
            if not (week_start <= start.date() <= week_end):
                continue
            if m.get("duration", 999) < min_minutes:
                continue
            video = z.pick_video(m)
            when = f"{start:%a %d/%m %I:%M %p}"
            if not video:
                issues += 1
                lines.append(f"   ! Zoom recording on {when} has no finished video yet")
                continue
            folder = z.find_day_folder(folders, start.date())
            prefix = z.zoom_file_prefix(video)
            if not folder or not z.already_uploaded(drive, folder["id"], prefix):
                issues += 1
                lines.append(f"   ! Zoom recording on {when} is NOT in Drive")
        lines.append("")

    if issues:
        lines.append(f"{issues} thing(s) to check, marked above.")
    else:
        lines.append("Everything recorded in Zoom last week is in Drive.")
    body = "\n".join(lines)

    subject = f"Weekly recordings summary {week_start:%d %b} - {week_end:%d %b}"
    if issues:
        subject += f" ({issues} to check)"

    print(subject + "\n\n" + body)
    if z.DRY_RUN or not to:
        print("\n[not sent - dry run or no summary_email_to in config.json]")
        return

    msg = MIMEText(body)
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"\nSent to {to}")


if __name__ == "__main__":
    main()
