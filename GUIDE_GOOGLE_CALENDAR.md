# How to Generate `credentials.json` for Google Calendar

To enable the "Sync to Calendar" feature, you need to create a free Google Cloud project and download your credentials. Follow these steps exactly:

### Step 1: Create a Project
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project dropdown in the top bar and select **"New Project"**.
3. Name it `LegalEase-Calendar` (or anything you like) and click **Create**.
4. Select your new project from the notification or dropdown.

### Step 2: Enable Calendar API
1. In the sidebar, go to **APIs & Services** > **Library**.
2. Search for **"Google Calendar API"**.
3. Click on it and click **Enable**.

### Step 3: Configure Consent Screen
1. Go to **APIs & Services** > **OAuth consent screen**.
2. Select **External** (unless you have a G-Suite organization) and click **Create**.
3. Fill in the required fields:
   - **App Name**: `LegalEase`
   - **User Support Email**: Your email
   - **Developer Contact Email**: Your email
4. Click **Save and Continue** until you reach the **Test Users** step.
5. Click **+ Add Users** and enter **YOUR specific Google email address**. (This is important! Only added emails can use the app in testing mode).
6. Click **Save and Continue** to finish.

### Step 4: Create Credentials
1. Go to **APIs & Services** > **Credentials**.
2. Click **+ Create Credentials** > **OAuth client ID**.
3. Application Type: Select **Desktop app**.
4. Name: `LegalEase Client`.
5. Click **Create**.

### Step 5: Download & Install
1. A popup will appear. Click the **Download JSON** button (it looks like a down arrow ⬇️).
2. Rename the downloaded file to exactly: `credentials.json`
3. Move this file into your project folder:
   `C:\Users\kashv\Documents\my_trae\pdf_extractor\`

### Step 6: Restart App
1. Stop the current app (Ctrl+C in terminal).
2. Run it again: `streamlit run app.py`.
3. Try the Sync button! A browser window will open asking you to allow access.
