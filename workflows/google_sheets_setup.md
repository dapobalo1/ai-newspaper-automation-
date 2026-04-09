# Setup Guide: Google Sheets Integration

## What you need
1. A Google Sheet (you create this — takes 30 seconds)
2. A `credentials.json` file from Google Cloud Console (one-time setup, ~5 minutes)

---

## Step 1 — Create the Google Sheet

1. Go to [sheets.google.com](https://sheets.google.com)
2. Click **"Blank"** to create a new spreadsheet
3. Rename it: click "Untitled spreadsheet" at the top → type `Atlantic Digest — Editorial Review`
4. Copy the Sheet ID from the URL bar:
   ```
   https://docs.google.com/spreadsheets/d/  1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms  /edit
                                              ↑ this is your GOOGLE_SHEET_ID
   ```
5. Paste that ID into `.env` after `GOOGLE_SHEET_ID=`

---

## Step 2 — Get Google API Credentials

### 2a. Create a Google Cloud project
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. At the top, click the project dropdown → **"New Project"**
3. Name it: `Atlantic Digest Bot` → click **"Create"**
4. Wait a few seconds, then make sure you're in that project (check the dropdown at top)

### 2b. Enable the Google Sheets API
1. In the left menu → **"APIs & Services"** → **"Library"**
2. Search for `Google Sheets API`
3. Click it → click **"Enable"**

### 2c. Create OAuth credentials
1. In the left menu → **"APIs & Services"** → **"Credentials"**
2. Click **"+ Create Credentials"** → **"OAuth client ID"**
3. If prompted to configure consent screen:
   - Click **"Configure Consent Screen"**
   - Choose **"External"** → click **"Create"**
   - App name: `Atlantic Digest Bot`
   - User support email: your email
   - Developer contact: your email
   - Click **"Save and Continue"** through the remaining steps
   - On the last screen click **"Back to Dashboard"**
4. Back on Credentials → **"+ Create Credentials"** → **"OAuth client ID"** again
5. Application type: **"Desktop app"**
6. Name: `Atlantic Digest Bot`
7. Click **"Create"**
8. Click **"Download JSON"**
9. Rename the downloaded file to `credentials.json`
10. Move it into the project folder:
    ```
    NewsPaper automation Demo/credentials.json
    ```

### 2d. Add yourself as a test user
1. Left menu → **"APIs & Services"** → **"OAuth consent screen"**
2. Scroll to **"Test users"** → click **"+ Add Users"**
3. Add your Gmail address → click **"Save"**

---

## Step 3 — First-time Authentication

Run this once:
```bash
python3 tools/export_to_sheets.py
```

A browser window will open asking you to sign in with Google and grant permission.
- Click your Google account
- Click **"Advanced"** → **"Go to Atlantic Digest Bot (unsafe)"** *(this is normal for dev apps)*
- Click **"Allow"**

This creates a `token.json` file in the project folder. You won't need to do this again — the token auto-refreshes.

---

## Step 4 — Share the Sheet with the Editorial Team

1. Open your Google Sheet
2. Click **"Share"** (top right)
3. Add the editor's email with **"Editor"** access
4. They can now open the sheet, read drafts, and type `APPROVE` or `REJECT` in the Approval column

---

## How the Approval Column Works

The editor sees each article as a row and types one of these in the **Approval** column:

| Type | Meaning |
|------|---------|
| `APPROVE` | Article is good — will be published to WordPress |
| `REJECT` | Article won't run — add reason in Editor Notes |
| `EDIT` | Needs changes — describe in Editor Notes column |
| *(blank)* | Not reviewed yet — will stay pending |

After the editor has reviewed, run:
```bash
python3 tools/check_approvals.py
```
This reads the sheet and updates the database. Approved articles are then ready to push to WordPress.

---

## Files Created by This Integration
| File | Purpose |
|------|---------|
| `credentials.json` | Google OAuth client secret (gitignored — never commit) |
| `token.json` | Auto-generated auth token (gitignored — never commit) |
