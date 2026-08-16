# setup_gdrive.py
# date created: 2026-08-16
# tags: #setup, #gdrive, #google-drive, #google-docs, #google-sheets, #google-tasks, #oauth

"""setup_gdrive.py — Set up Google Drive, Docs, Sheets, and Tasks access for the Evelyn Engine.

Guides the user to configure Google Cloud Console credentials, runs an OAuth2 flow,
and saves the resulting token to the configured token path.
"""

import os
import shutil
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evelyn_config as cfg

from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes requested: Drive/Docs/Sheets (readonly) and Tasks (Full)
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/tasks",
]


def main():
    print("=" * 60)
    print(" Google Drive & Workspace Sync Authentication Setup")
    print("=" * 60)

    cred_path = cfg.GDRIVE_CREDENTIALS_PATH
    token_path = cfg.GDRIVE_TOKEN_PATH

    # Ensure data folder exists
    os.makedirs(os.path.dirname(token_path), exist_ok=True)

    # Check if credentials exist at GDRIVE_CREDENTIALS_PATH or fallback to GCAL_CREDENTIALS_PATH
    if not os.path.exists(cred_path):
        if os.path.exists(cfg.GCAL_CREDENTIALS_PATH):
            print(f"Found existing Google credentials at: {cfg.GCAL_CREDENTIALS_PATH}")
            print(f"Copying to {cred_path} for Drive/Workspace authentication...")
            shutil.copy(cfg.GCAL_CREDENTIALS_PATH, cred_path)
        else:
            print(f"Error: Credentials file not found at: {cred_path}\n")
            print("To configure Google Drive & Workspace access:")
            print("1. Go to Google Cloud Console: https://console.cloud.google.com/")
            print("2. Enable the following APIs in your project:")
            print("   - Google Drive API")
            print("   - Google Docs API")
            print("   - Google Sheets API")
            print("   - Google Tasks API")
            print("3. Configure the OAuth Consent Screen (type 'External', publish status 'Testing').")
            print("4. Add your own Google account as a Test User.")
            print("5. Go to Credentials -> Create Credentials -> OAuth Client ID.")
            print("6. Select Application Type: 'Desktop App'.")
            print("7. Download the client secret JSON file.")
            print("8. Place it at:")
            print(f"   {cred_path}")
            print("9. Re-run this script.")
            print("=" * 60)
            return

    print(f"Using credentials at: {cred_path}")
    print("Requested Scopes:")
    for scope in SCOPES:
        print(f"  - {scope}")
    print("\nStarting authentication flow...")
    print("Please follow the instructions in your browser to authorize access.")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
        creds = flow.run_local_server(port=0)

        # Save token
        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

        print("\n" + "=" * 60)
        print(" SUCCESS! Authentication token successfully saved.")
        print(f" Token Path: {token_path}")
        print(" Evelyn can now access Google Drive, Docs, Sheets, and Tasks.")
        print("=" * 60)
    except Exception as e:
        print(f"\nError running OAuth flow: {e}")
        print("=" * 60)


if __name__ == "__main__":
    main()
