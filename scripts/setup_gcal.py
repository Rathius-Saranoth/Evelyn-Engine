# setup_gcal.py
# date created: 2026-06-19
# date modified: 2026-06-19
# tags: #setup, #gcal, #google-calendar, #oauth

"""setup_gcal.py — Set up Google Calendar Read access for the Evelyn Engine.

Guides the user to configure Google Cloud Console credentials, runs a local
OAuth2 flow, and saves the resulting token to the configured data folder path.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from google_auth_oauthlib.flow import InstalledAppFlow

import evelyn_config as cfg

SCOPES = ["https://www.googleapis.com/auth/calendar"]

def main():
    print("=" * 60)
    print(" Google Calendar Sync Authentication Setup")
    print("=" * 60)

    cred_path = cfg.GCAL_CREDENTIALS_PATH
    token_path = cfg.GCAL_TOKEN_PATH

    # Ensure data folder exists
    os.makedirs(os.path.dirname(cred_path), exist_ok=True)

    if not os.path.exists(cred_path):
        print(f"Error: Credentials file not found at: {cred_path}\n")
        print("To configure Google Calendar access:")
        print("1. Go to Google Cloud Console: https://console.cloud.google.com/")
        print("2. Create a project and enable the 'Google Calendar API'.")
        print("3. Configure the OAuth Consent Screen (type 'External', publish status 'Testing').")
        print("4. Add your own Google account as a Test User.")
        print("5. Go to Credentials -> Create Credentials -> OAuth Client ID.")
        print("6. Select Application Type: 'Desktop App'.")
        print("7. Download the client secret JSON file.")
        print("8. Rename it to 'gcal_credentials.json' and place it in:")
        print(f"   {cred_path}")
        print("9. Re-run this script.")
        print("=" * 60)
        return

    print(f"Found credentials at: {cred_path}")
    print("Starting authentication flow...")
    print("Please follow the instructions in your browser to authorize calendar access.")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
        creds = flow.run_local_server(port=0)

        # Save token
        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

        print("\n" + "=" * 60)
        print(" SUCCESS! Authentication token successfully saved.")
        print(f" Token Path: {token_path}")
        print(" Evelyn can now read and sync Google Calendar events in the background.")
        print("=" * 60)
    except (OSError, ValueError, RuntimeError) as e:
        print(f"\nError running OAuth flow: {e}")
        print("=" * 60)

if __name__ == "__main__":
    main()
