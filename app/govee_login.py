"""One-time interactive Govee app login (handles the 2FA email code).

Usage:
  python -m app.govee_login           # triggers the verification email
  python -m app.govee_login 123456    # completes login with the emailed code

On success the account token is cached in data/govee_account.json and the
main app uses it without needing the code again until it expires.
"""
import sys

from . import config
from .govee_iot import GoveeIoT, TwoFactorRequired

if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else None
    client = GoveeIoT(config.GOVEE_EMAIL, config.GOVEE_PASSWORD, {})
    try:
        acct = client._login(code)
        print(f"Login OK — account id {acct['account_id']}, token cached.")
    except TwoFactorRequired as e:
        print(f"2FA: {e}")
        sys.exit(1)
