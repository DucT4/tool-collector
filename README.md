# TikTok Comment Collector

Python tool to collect public TikTok comments that are visible in an existing Chrome/GPMLogin browser through CDP.

## Setup

```powershell
python -m pip install -r requirements.txt
```

Open Chrome with CDP:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="C:\chrome-cdp-profile"
```

For GPMLogin, set `CDP_URL` to the profile CDP port.

Copy `.env.example` to `.env` and adjust MongoDB/CDP values.

## Run

```powershell
python main.py "https://www.tiktok.com/@user/video/123456789" --debug
```

The tool does not bypass login, captcha, or access controls. It only reads public content rendered in the browser DOM.

## Selector Updates

TikTok changes DOM often. Update selectors in `config/tiktok_selectors.py` after inspecting a real video page with Chrome DevTools. With `--debug`, empty collection runs write selector candidates and screenshots to `logs/`.
