# Google Calendar Integration Setup

This guide explains how to set up Google Calendar sync for VoiceDesk booking confirmations.

## Step 1: Google Cloud Console Setup

1. **Create a new project**
   - Go to [console.cloud.google.com](https://console.cloud.google.com)
   - Click on the project selector at the top
   - Click "NEW PROJECT"
   - Name it "VoiceDesk"
   - Click "CREATE"

2. **Enable the Google Calendar API**
   - In the console, go to "APIs & Services" → "Library"
   - Search for "Google Calendar API"
   - Click on it and press "ENABLE"

3. **Create OAuth 2.0 Client ID**
   - Go to "APIs & Services" → "Credentials"
   - Click "Create Credentials" → "OAuth Client ID"
   - If prompted, first configure the OAuth consent screen:
     - Choose "External" for User Type
     - Fill in App name: "VoiceDesk"
     - Add your email as support contact
     - Add scope: `https://www.googleapis.com/auth/calendar.events`
     - Skip optional fields and publish
   - Return to Credentials and create OAuth Client ID:
     - Application type: **Web application**
     - Name: "VoiceDesk Web"
     - Authorized JavaScript origins: `http://localhost:8000`
     - Authorized redirect URIs: `http://localhost:8000/auth/google/callback`
     - Click "CREATE"

4. **Download credentials**
   - Click the created client ID
   - Click "DOWNLOAD JSON" (button at top right)
   - Save the file as `credentials.json` in the project root (same folder as `main.py`)

## Step 2: Local Setup

1. **Add credentials to .gitignore**
   - Open `.gitignore` in the project root
   - Add these lines:
     ```
     credentials.json
     token.json
     ```

2. **Install dependencies**
   - Run: `pip install -r requirements.txt`
   - This will install the Google Calendar API client and authentication libraries:
     - google-auth
     - google-auth-oauthlib
     - google-api-python-client

## Step 3: Authorize VoiceDesk

1. **Start the server**
   - Run: `python main.py` (or however you start it)
   - Server should be at `http://localhost:8000`

2. **Visit the auth URL**
   - Open your browser and go to: `http://localhost:8000/auth/google`
   - This redirects you to Google's consent screen
   - Click "Allow" to grant VoiceDesk permission to manage your calendar
   - You'll be redirected back to `/dashboard`
   - A `token.json` file will be created automatically

3. **Verify connection**
   - In the dashboard, you should see a green "Calendar synced" badge in the header
   - Calendar sync is now active!

## Usage

- **Automatic sync**: When a booking is confirmed via Sofia (the AI agent), an event is automatically created in your Google Calendar
- **Event details**: Events show party size, guest name, phone number, special requests, and allergy notes
- **Cancellations**: If a booking is cancelled via the dashboard, the calendar event is automatically deleted
- **Colors**: Events are color-coded green for easy identification

## Troubleshooting

**"Calendar not connected" badge**
- Visit `http://localhost:8000/auth/google` again to re-authorize
- Check that `credentials.json` exists in the project root
- Check that `token.json` was created after authorization

**"CalendarNotConnected" error in logs**
- This means the authorization flow hasn't been completed yet
- Visit `http://localhost:8000/auth/google` to complete authorization

**Event not created**
- Calendar sync is non-critical, so bookings confirm even if sync fails
- Check server logs for "[CALENDAR]" messages to debug
- Ensure your Google Calendar has "Create Events" permission

**Dates/times are wrong**
- VoiceDesk uses Europe/Paris timezone for events
- Ensure your Google Calendar and system timezone settings are correct

## Environment Variables

No additional environment variables needed. VoiceDesk uses local files:
- `credentials.json` — OAuth credentials from Google Cloud Console
- `token.json` — Cached refresh token (auto-created after first auth)
