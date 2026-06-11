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
GET http://127.0.0.1:9495/api/v1/profiles/start/{profile_id}
```

Then it reads `remote_debugging_port` or `remote_debugging_address` from the API response and connects Playwright to:

```text
http://127.0.0.1:{remote_debugging_port}
```

You can also set these in `.env`:

```env
GPM_API_BASE=http://127.0.0.1:9495
GPM_PROFILE_ID=YOUR_PROFILE_ID
```

If you see `Cannot connect to GPMLogin API`, the GPMLogin REST API is not listening on the configured port. Open GPMLogin, check Settings > API Gateway > Local API url, then update `GPM_API_BASE` or pass:

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

For videos with many comments or replies, raise the soft scroll and reply-click budgets:

```powershell
python main.py "https://www.tiktok.com/@user/video/123456789" `
  --scroll-times 80 `
  --max-reply-clicks 1000 `
  --debug
```

Use `--max-reply-clicks 0` to keep opening visible reply expanders until none remain. The tool still keeps an internal hard cap to avoid hanging on broken pages.

The collector watches TikTok comment/reply network calls such as `comment/list` and `comment/list/reply`.
It only stops after the comment DOM stops growing, no tracked comment/reply request is pending, and no visible reply expander remains. If `--debug` is enabled, the output includes `comment_network_requests`, `pending_comment_network`, `reply_clicks`, `scroll_rounds`, and `stop_reason`.

## Monitor and Telegram Alerts

Set Telegram values in `.env`:

```env
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
```

Run a foreground monitor that checks every 5 minutes:

```powershell
python main.py "https://www.tiktok.com/@user/video/123456789" `
  --monitor `
  --interval-minutes 5 `
  --debug
```

The first run seeds MongoDB for a new video and sends a short seeded summary. Later runs send only new parent comments and new replies.

The tool does not bypass login, captcha, or access controls. It only reads public content rendered in the browser DOM.

## Selector Updates

TikTok changes DOM often. Update selectors in `config/tiktok_selectors.py` after inspecting a real video page with Chrome DevTools. With `--debug`, empty collection runs write selector candidates and screenshots to `logs/`.
