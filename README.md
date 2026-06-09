# TikTok Comment Collector

Python tool to collect public TikTok comments that are visible in Chrome/GPMLogin through CDP.

## Setup

```powershell
python -m pip install -r requirements.txt
```

## GPMLogin Flow

Keep the GPMLogin app open, then run with a profile id:

```powershell
python main.py "https://www.tiktok.com/@user/video/123456789" `
  --gpm-profile-id "YOUR_PROFILE_ID" `
  --debug
```

The tool calls:

```text
GET http://127.0.0.1:19995/api/v3/profiles/start/{profile_id}
```

Then it reads `remote_debugging_port` from the API response and connects Playwright to:

```text
http://127.0.0.1:{remote_debugging_port}
```

You can also set these in `.env`:

```env
GPM_API_BASE=http://127.0.0.1:19995
GPM_PROFILE_ID=YOUR_PROFILE_ID
```

If you see `Cannot connect to GPMLogin API at http://127.0.0.1:19995`, the GPMLogin REST API is not listening on that port. Open GPMLogin, enable/start the local API, then either update `GPM_API_BASE` or pass:

```powershell
python main.py "https://www.tiktok.com/@user/video/123456789" `
  --gpm-profile-id "YOUR_PROFILE_ID" `
  --gpm-api-base "http://127.0.0.1:YOUR_GPM_API_PORT" `
  --debug
```

If the GPMLogin profile is already open and you know its CDP port, bypass the GPMLogin API and connect directly:

```powershell
python main.py "https://www.tiktok.com/@user/video/123456789" `
  --cdp-url "http://127.0.0.1:REMOTE_DEBUGGING_PORT" `
  --debug
```

## Manual Chrome CDP Flow

Open Chrome with CDP if you do not use GPMLogin:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="C:\chrome-cdp-profile"
```

Copy `.env.example` to `.env` and adjust MongoDB/CDP values.

## Run

```powershell
python main.py "https://www.tiktok.com/@user/video/123456789" --debug
```

The tool does not bypass login, captcha, or access controls. It only reads public content rendered in the browser DOM.

## Selector Updates

TikTok changes DOM often. Update selectors in `config/tiktok_selectors.py` after inspecting a real video page with Chrome DevTools. With `--debug`, empty collection runs write selector candidates and screenshots to `logs/`.
