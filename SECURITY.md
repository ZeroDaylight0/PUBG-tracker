# Security Policy

## Supported versions

Only the latest version on the `main` branch receives fixes.

## API key handling

PUBG Tracker is designed so that your PUBG API key stays on the local machine:

- The local server (`app.py`) reads the key and proxies requests to the official API.
- The key is never embedded in the HTML/JS served to the browser.
- In the GitHub Pages demo the key you paste is used only from your own browser
  and is not stored or sent anywhere except `api.pubg.com`.

Never commit a real API key. If you accidentally did, revoke it at
[developer.pubg.com](https://developer.pubg.com/) and generate a new one.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.
Use [GitHub private vulnerability reporting](https://github.com/ZeroDaylight0/PUBG-tracker/security/advisories/new)
and include steps to reproduce. You should get a response within a few days.
