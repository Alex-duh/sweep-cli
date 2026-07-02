"""
auth.py — handles Google OAuth and returns a ready-to-use Gmail API service.

Why a separate module?
  sweep.py and classify.py both need the Gmail service object.
  Keeping auth in one place means we change it once if Google ever
  updates their auth flow.
"""

import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# The OAuth scope defines what the app is allowed to do.
# gmail.modify = can read emails AND change labels (archive) AND send mail.
# We upgraded from gmail.readonly (Step 1) because archiving requires
# the messages.modify endpoint, and unsubscribing via mailto requires messages.send.
# NOTE: gmail.modify cannot permanently delete emails — that needs gmail.readonly
# plus a separate delete scope, which we intentionally do NOT include.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_FILE = "credentials.json"  # downloaded from Google Cloud Console
TOKEN_FILE = "token.json"              # saved here after the first login


def get_service():
    """
    Authenticate with Google and return a Gmail API service object.

    First run: opens a browser tab so you can approve access.
    Later runs: silently reuses the saved token in token.json.
    """
    creds = None

    # If token.json already exists, load the saved credentials from it.
    # This avoids opening a browser tab on every single run.
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Check if we need fresh credentials.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # The token expired but we have a refresh token Google gave us.
            # We can silently get a new access token without user interaction.
            creds.refresh(Request())
        else:
            # No saved token at all — do the full browser login flow.
            # run_local_server(port=0) picks a free port automatically.
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save the credentials so the next run skips the browser step.
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    # build() creates the service object. All Gmail API calls go through it.
    # "gmail" = which API, "v1" = which version (only version that exists).
    return build("gmail", "v1", credentials=creds)


# This block only runs when you do: python auth.py
# It does NOT run when sweep.py imports auth (that's what `if __name__` means).
if __name__ == "__main__":
    service = get_service()

    # users().getProfile() is the simplest Gmail API call —
    # it just returns the account's email address and message counts.
    profile = service.users().getProfile(userId="me").execute()

    print(f"✓ Authenticated as: {profile['emailAddress']}")
    print(f"  Total messages in account: {profile['messagesTotal']}")
