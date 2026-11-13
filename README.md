# Zoom recordings → Google Drive

Two scheduled jobs on GitHub Actions:

| Job | When | What it does |
|---|---|---|
| Zoom recordings to Drive | Daily, once per batch (see below) | Copies that batch's Zoom cloud recordings into its Drive folder, in a new `DayN - DD/MM/YYYY` folder |
| Weekly recordings summary | Monday, 7:00 AM IST | Emails uday@moventai.com what each batch got last week and flags anything missing |

Batches (already set in `config.json`):

| Batch (key) | Class | Copied at (IST) | Zoom meeting | Host | Drive folder |
|---|---|---|---|---|---|
| Playwright weekend (`pw-weekend`) | Sat–Sun 10:30 AM | 3:00 PM | 870 2113 9509 | uday@moventai.com | Playwright Batch 17th Oct weekend recordings |
| Playwright weekday (`pw-weekday`) | Mon, Tue, Thu, Fri 4:30 PM | 7:30 PM | 839 3892 6362 | uday@moventai.com | Playwright Batch 13th Oct weekday recordings |
| AI/ML (`ai`) | Mon–Fri 7:00 PM | 10:00 PM | 831 5061 5801 | takmogevijay99@gmail.com (Vijay) | AI Batch 5th October Recordings |

Each batch's run happens every day at its time; on days without a class it finds nothing. Each run also re-checks the previous 2 days, so if Zoom was still processing a recording, the next run picks it up. To change a time, edit the `cron` lines in `.github/workflows/zoom-to-drive.yml` (times there are UTC = IST − 5:30).

How it behaves:
- New day folders continue your numbering (highest DayN + 1). If a folder for that date already exists, the video goes into it.
- Files keep Zoom's own name (`GMT20261112-110039_Recording.mp4`), same as your manual uploads.
- A recording already in the day folder (including ones you uploaded by hand) is skipped, so nothing is duplicated.
- Recordings shorter than 10 minutes (false starts) are skipped.
- Only classes from 13 Nov 2026 onwards are handled.
- If a run fails, GitHub emails you.

---

## 1. Zoom app → 3 secrets  (signed in as uday@moventai.com, account owner/admin)

1. Go to https://marketplace.zoom.us → **Develop** (top right) → **Build App** → **Server-to-Server OAuth App** → name it `Recordings to Drive` → Create.
2. **App Credentials** page — copy:
   - Account ID → `ZOOM_ACCOUNT_ID`
   - Client ID → `ZOOM_CLIENT_ID`
   - Client Secret → `ZOOM_CLIENT_SECRET`
3. **Information** page — fill company name, developer name and email (required to activate).
4. **Scopes** page → **Add Scopes** → search `recording` → tick the one for **listing user recordings, admin level** (`cloud_recording:read:list_user_recordings:admin`). Save.
   (Only if you later set `delete_from_zoom_after_upload` to true: also add the delete-recording admin scope.)
5. **Activation** page → **Activate your app**.

The app is account-level, so it can read Vijay's recordings too.

## 2. Google credentials → 3 secrets  (signed in as uday@moventai.com)

**a. Create the project and client**
1. https://console.cloud.google.com → project picker (top left) → **New Project** → name `moventai-recordings` → Create, then select it.
2. Search bar → **Google Drive API** → Enable. Then search **Gmail API** → Enable.
3. Menu → **APIs & Services → OAuth consent screen** (may show as **Google Auth Platform**) → Get started → app name `Recordings to Drive`, support email uday@moventai.com → Audience: **Internal** → contact email → Create.
   *Internal* = only moventai.com accounts can use it, no Google review, and the sign-in doesn't expire.
4. **Clients** (or **Credentials → Create credentials → OAuth client ID**) → Application type **Web application** → under **Authorized redirect URIs** add:
   `https://developers.google.com/oauthplayground`
   → Create. Copy:
   - Client ID → `GDRIVE_CLIENT_ID`
   - Client secret → `GDRIVE_CLIENT_SECRET`

**b. Get the refresh token (no code needed)**
1. Open https://developers.google.com/oauthplayground
2. Click the **gear icon** (top right) → tick **Use your own OAuth credentials** → paste the Client ID and Client secret → Close.
3. Left side, in the box **Input your own scopes**, paste exactly:
   `https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/gmail.send`
   → **Authorize APIs** → sign in as uday@moventai.com → **Allow**.
4. Step 2 → **Exchange authorization code for tokens**. Copy the **Refresh token** → `GDRIVE_REFRESH_TOKEN`.

## 3. Put the 6 secrets in GitHub

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**, once per row. Name must match exactly.

| Name | Value from |
|---|---|
| `ZOOM_ACCOUNT_ID` | Step 1.2 |
| `ZOOM_CLIENT_ID` | Step 1.2 |
| `ZOOM_CLIENT_SECRET` | Step 1.2 |
| `GDRIVE_CLIENT_ID` | Step 2a.4 |
| `GDRIVE_CLIENT_SECRET` | Step 2a.4 |
| `GDRIVE_REFRESH_TOKEN` | Step 2b.4 |

GitHub secrets are encrypted and never shown in logs, even in a public repo. Don't paste them anywhere else in the repo.

## 4. Upload the files

Upload everything in this folder to the repo root. The two workflow files must end up at:
```
.github/workflows/zoom-to-drive.yml
.github/workflows/weekly-summary.yml
```
Mac/Windows often hide the `.github` folder when dragging. If it doesn't appear in the repo, use **Add file → Create new file**, type `.github/workflows/zoom-to-drive.yml` as the name, paste the file's contents, commit. Repeat for `weekly-summary.yml`.

## 5. Test

1. **Actions** tab → **Zoom recordings to Drive** → **Run workflow**, batch *all*, *Dry run* ticked → open the run log. It lists what it would copy and which folders it would create.
2. Run again with *Dry run* unticked for a real copy.
3. **Weekly recordings summary** → Run workflow with *Dry run* ticked to see the email text in the log; untick to get a real email.

After that both run on schedule. GitHub can start scheduled runs 10–30 min late at busy times.

## Settings in config.json

- `start_from_date` — older recordings are ignored.
- `lookback_days` — how many days back each daily run checks (2).
- `skip_recordings_shorter_than_minutes` — false-start filter (10).
- `delete_from_zoom_after_upload` — `false`. Set `true` later to move the Zoom cloud copy to Zoom trash after upload (recoverable for 30 days) and free Zoom storage. Needs the extra Zoom scope.
- `summary_email_to` — who gets the Monday email.
- New batch: add a block with its name, meeting ID, host email and Drive folder ID.
