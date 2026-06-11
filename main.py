import argparse
import sys
import time

from browser.cdp import GPMLoginError, connect_to_browser, connect_to_gpm_profile
from collectors.tiktok_comment_collector import collect_from_video, normalize_source_url
from config.settings import settings
from database.mongo import get_active_telegram_chat_ids, get_collection, get_telegram_subscriber_collection, save_comments
from monitors.tiktok_monitor import save_and_diff_comments
from notifications.telegram import (
    TelegramConfigError,
    TelegramNotifier,
    TelegramSubscriptionService,
    format_comment_notifications,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect public TikTok comments from a CDP browser.")
    parser.add_argument("urls", nargs="+", help="TikTok video URL(s) to collect.")
    parser.add_argument("--cdp-url", default=settings.cdp_url, help="Chrome/GPMLogin CDP URL.")
    parser.add_argument(
        "--gpm-profile-id",
        default=settings.gpm_profile_id,
        help="GPMLogin profile id. When provided, the tool starts this profile through GPMLogin API.",
    )
    parser.add_argument("--gpm-api-base", default=settings.gpm_api_base, help="GPMLogin local API base URL.")
    parser.add_argument("--scroll-times", type=int, default=settings.scroll_times)
    parser.add_argument("--max-reply-clicks", type=int, default=settings.max_reply_clicks)
    parser.add_argument("--monitor", action="store_true", help="Run forever and check URLs repeatedly.")
    parser.add_argument("--interval-minutes", type=float, default=5, help="Monitor interval in minutes (default: 5).")
    parser.add_argument("--debug", action="store_true", help="Dump selector candidates and screenshot on empty results.")
    args = parser.parse_args(argv)
    args.cdp_url_provided = any(arg == "--cdp-url" or arg.startswith("--cdp-url=") for arg in argv)
    return args


def connect_context(args: argparse.Namespace):
    use_gpm = bool(args.gpm_profile_id and not args.cdp_url_provided)
    if use_gpm:
        (playwright, _browser, context), cdp_url = connect_to_gpm_profile(args.gpm_profile_id, args.gpm_api_base)
        print(f"[GPM] profile={args.gpm_profile_id} cdp={cdp_url}")
    else:
        playwright, _browser, context = connect_to_browser(args.cdp_url)
        print(f"[CDP] cdp={args.cdp_url}")
    return playwright, context


def print_connection_error(args: argparse.Namespace, exc: GPMLoginError) -> None:
    if args.gpm_profile_id and not args.cdp_url_provided:
        print(f"[GPM ERROR] {exc}")
        print("[HINT] Open GPMLogin, enable/start its local API, then pass the correct API base:")
        print("[HINT] python main.py <url> --gpm-profile-id <id> --gpm-api-base http://127.0.0.1:<api_port>")
        print("[HINT] If the profile is already open, use --cdp-url http://127.0.0.1:<remote_debugging_port> instead.")
    else:
        print(f"[CDP ERROR] {exc}")
        print("[HINT] Start Chrome/GPMLogin with a live remote debugging port, then pass:")
        print("[HINT] python main.py <url> --cdp-url http://127.0.0.1:<remote_debugging_port>")


def collect_url(page, url: str, args: argparse.Namespace) -> tuple[str, list[dict], float]:
    start = time.perf_counter()
    source_url = normalize_source_url(url)
    print(f"[START] {source_url}")
    comments = collect_from_video(
        page,
        url,
        scroll_times=args.scroll_times,
        max_reply_clicks=args.max_reply_clicks,
        debug=args.debug,
    )
    elapsed = time.perf_counter() - start
    return source_url, comments, elapsed


def run_once(args: argparse.Namespace, context, collection) -> None:
    page = context.new_page()
    try:
        for url in args.urls:
            try:
                source_url, comments, elapsed = collect_url(page, url, args)
                saved = save_comments(comments, source_url, collection=collection, prune_stale=False)
                reply_count = sum(len(item.get("replies", [])) for item in comments)
                total_items = len(comments) + reply_count
                print(
                    f"[OK] collected={len(comments)} replies={reply_count} total_items={total_items} "
                    f"saved={saved} elapsed={elapsed:.1f}s"
                )
            except Exception as exc:
                source_url = normalize_source_url(url)
                print(f"[ERROR] {source_url} error={exc}")
    finally:
        page.close()


def is_browser_connection_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "target page, context or browser has been closed",
            "browsercontext.new_page: target",
            "browser has been closed",
            "connection closed",
            "connection refused",
            "cdp endpoint is not ready",
        )
    )


def telegram_recipient_chat_ids(notifier: TelegramNotifier, subscriber_collection) -> list[str]:
    chat_ids = set(get_active_telegram_chat_ids(subscriber_collection))
    if notifier.chat_id:
        chat_ids.add(str(notifier.chat_id))
    return sorted(chat_ids)


def broadcast_telegram_messages(notifier: TelegramNotifier, subscriber_collection, messages: list[str]) -> int:
    sent = 0
    for chat_id in telegram_recipient_chat_ids(notifier, subscriber_collection):
        try:
            notifier.send_messages(messages, chat_id=chat_id)
            sent += 1
        except Exception as exc:
            print(f"[TELEGRAM ERROR] chat_id={chat_id} error={exc}")
    if sent == 0:
        print("[TELEGRAM] no active subscribers; ask a recipient to send /start to the bot")
    return sent


def notification_messages_for_diff(source_url: str, diff) -> list[str]:
    if diff.seeded or not (diff.new_comments or diff.new_replies):
        return []
    return format_comment_notifications(source_url, diff.new_comments, diff.new_replies)


def poll_telegram_subscriptions(service: TelegramSubscriptionService) -> None:
    try:
        processed = service.poll_once()
        if processed:
            print(f"[TELEGRAM] processed_subscriptions={processed}")
    except Exception as exc:
        print(f"[TELEGRAM POLL ERROR] {exc}")


def wait_with_telegram_poll(seconds: float, service: TelegramSubscriptionService, poll_seconds: float = 5) -> None:
    deadline = time.monotonic() + max(seconds, 0)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll_seconds, remaining))
        poll_telegram_subscriptions(service)


def run_monitor(args: argparse.Namespace, collection, subscriber_collection) -> None:
    notifier = TelegramNotifier()
    if not notifier.enabled():
        raise TelegramConfigError("Monitor mode requires TELEGRAM_BOT_TOKEN")
    subscription_service = TelegramSubscriptionService(notifier, subscriber_collection)

    interval_seconds = max(args.interval_minutes, 0.1) * 60
    round_index = 0
    playwright = None
    context = None

    try:
        while True:
            poll_telegram_subscriptions(subscription_service)
            round_index += 1
            print(f"[MONITOR] round={round_index} urls={len(args.urls)}")
            page = None
            reconnect_required = False
            try:
                if context is None:
                    playwright, context = connect_context(args)
                page = context.new_page()

                for url in args.urls:
                    source_url = normalize_source_url(url)
                    try:
                        source_url, comments, elapsed = collect_url(page, url, args)
                        diff = save_and_diff_comments(comments, source_url, collection)
                        reply_count = sum(len(item.get("replies", [])) for item in comments)
                        print(
                            f"[MONITOR OK] comments={len(comments)} replies={reply_count} "
                            f"new_comments={len(diff.new_comments)} new_replies={len(diff.new_replies)} "
                            f"seeded={diff.seeded} elapsed={elapsed:.1f}s"
                        )

                        notification_messages = notification_messages_for_diff(source_url, diff)
                        if notification_messages:
                            broadcast_telegram_messages(
                                notifier,
                                subscriber_collection,
                                notification_messages,
                            )
                    except Exception as exc:
                        print(f"[MONITOR ERROR] {source_url} error={exc}")
                        reconnect_required = is_browser_connection_error(exc)
                        if reconnect_required:
                            break
            except Exception as exc:
                reconnect_required = True
                print(f"[MONITOR CONNECTION ERROR] {exc}")
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass

            if reconnect_required:
                if playwright is not None:
                    try:
                        playwright.stop()
                    except Exception:
                        pass
                playwright = None
                context = None
                reconnect_delay = min(interval_seconds, 15)
                print(f"[MONITOR] reconnecting in {reconnect_delay:g} seconds")
                wait_with_telegram_poll(reconnect_delay, subscription_service)
                continue

            print(f"[MONITOR] sleeping {args.interval_minutes:g} minutes")
            wait_with_telegram_poll(interval_seconds, subscription_service)
    finally:
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    playwright = None

    try:
        collection = get_collection()
        if args.monitor:
            subscriber_collection = get_telegram_subscriber_collection()
            run_monitor(args, collection, subscriber_collection)
        else:
            try:
                playwright, context = connect_context(args)
            except GPMLoginError as exc:
                print_connection_error(args, exc)
                return 1
            run_once(args, context, collection)

        return 0
    except TelegramConfigError as exc:
        print(f"[TELEGRAM ERROR] {exc}")
        return 1
    except KeyboardInterrupt:
        print("[STOP] interrupted")
        return 0
    finally:
        if playwright:
            playwright.stop()


if __name__ == "__main__":
    raise SystemExit(main())
