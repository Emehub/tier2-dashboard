# Put the dashboard on Streamlit

```
Kobo ──► Apps Script (already running, nothing to change) ──► Google Sheet ──► Streamlit app ──► your team
```

The Kobo → Google Sheet sync keeps running exactly as it does now. Streamlit only **reads** the Sheet, checks it every minute, and shows the same dashboard.

## Files in this folder (upload all of them to GitHub)

| File | What it is |
|---|---|
| `app.py` | The Streamlit app: reads the Sheet and shows the dashboard |
| `dashboard.html` | The dashboard itself |
| `requirements.txt` | Tells Streamlit which Python packages to install |
| `.streamlit/config.toml` | Light theme and minimal toolbar |
| `.streamlit/secrets.toml.example` | Template for the secrets, for reference only (the real secrets go into Streamlit, not GitHub) |

---

## Step 1: Google Cloud: create the "robot" account that reads the Sheet (about 15 min)

1. **Pick a project.** Go to **console.cloud.google.com** and choose a project at the top, or create one, e.g. `tier2-dashboard`.
2. **Turn on the Sheets API.** Go to **APIs & Services → Library**, search **Google Sheets API**, and click **Enable**.
3. **Create the service account.** Go to **IAM & Admin → Service Accounts → + Create service account**.
   - Name: `tier2-dashboard-reader`
   - Click **Create and continue**, skip the roles, then click **Done**.
4. **Download its key.** Click the new service account, open the **Keys** tab, choose **Add key → Create new key → JSON → Create**. A `.json` file downloads.
   **Keep this file private:** it's a password. Never upload it to GitHub.
5. **Copy its email address.** It looks like `tier2-dashboard-reader@tier2-dashboard.iam.gserviceaccount.com`.
6. **Share the Sheet with it.** Open the Google Sheet **Ghana Tier 2 – Dashboard Data**, click **Share**, paste that email, and set it to **Viewer**. Untick "Notify", then click **Share**.

If step 4 says key creation is blocked by an organisation policy, your Google Workspace admin needs to allow service-account keys for this project.

## Step 2: GitHub: put the app files online (about 5 min)

1. On **github.com**, click **+ → New repository**.
2. Name: `tier2-dashboard`. Select **Private**, then click **Create repository**.
3. On the new repository page, click **uploading an existing file**.
4. Open the `streamlit_app` folder on your computer and **drag everything inside it** into the browser, including the `.streamlit` folder.
   - If `.streamlit` is hidden in File Explorer, go to **View → Show → Hidden items**.
5. Click **Commit changes**.

## Step 3: Streamlit Community Cloud: create the app (about 5 min)

1. Go to **share.streamlit.io** and click **Create app** (or **New app**), then choose to deploy from GitHub.
2. **Repository:** `your-github-name/tier2-dashboard`. **Branch:** `main`. **Main file path:** `app.py`.
3. Click **Advanced settings**, then **Secrets**, and paste:
   ```
   SHEET_ID = "the long code from the Sheet's address"

   GCP_SERVICE_ACCOUNT_JSON = '''
   (open the .json key file in Notepad, copy ALL of it, paste it here)
   '''
   ```
   The Sheet ID is the part between `/d/` and `/edit` in the Sheet's address.
4. Click **Deploy**. The first start takes a minute or two.

## Step 4: Decide who can see it

Because the repository is **private**, the app is private too. Open your app's **Share** (or Settings → Sharing) and **invite viewers by email**. They sign in to see the dashboard. Check Streamlit's current free-plan limits for private apps there.

---

## Day-to-day

- **New Kobo submissions** appear on the dashboard within about 10–11 minutes: up to 10 minutes for the Kobo → Sheet sync, plus up to 1 minute for Streamlit's check.
- **The page only reloads when the data actually changes,** so viewers keep their place.
- **The Sheet's `config_*` tabs and the feedback `status` column** still control sentinels, quotas, actions and settings, exactly as before.

## Updating the dashboard later

When `dashboard.html` or `app.py` changes:
1. In the GitHub repository, click **Add file → Upload files**.
2. Drag in the changed file and click **Commit changes**.

Streamlit notices and redeploys automatically. There's nothing to paste into Apps Script.

## If something's wrong

The app shows a plain message instead of the dashboard:

| Message | Fix |
|---|---|
| "The Google Sheet was not found" | Check `SHEET_ID`, and that the Sheet is shared with the service-account email |
| "Google refused access" | Enable the Google Sheets API (step 1.2), and check the Sheet is shared |
| "A secret is missing" | Check the Secrets box spelling: `SHEET_ID` and `GCP_SERVICE_ACCOUNT_JSON` |
