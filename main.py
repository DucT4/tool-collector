import argparse
import sys
import time

from browser.cdp import GPMLoginError, connect_to_browser, connect_to_gpm_profile
from collectors.tiktok_comment_collector import collect_from_video, normalize_source_url
from config.settings import settings
from database.mongo import get_collection, save_comments


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
    parser.add_argument("--debug", action="store_true", help="Dump selector candidates and screenshot on empty results.")
    args = parser.parse_args(argv)
    args.cdp_url_provided = any(arg == "--cdp-url" or arg.startswith("--cdp-url=") for arg in argv)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    playwright = None
    page = None

    try:
        try:
            use_gpm = bool(args.gpm_profile_id and not args.cdp_url_provided)
            if use_gpm:
                (playwright, _browser, context), cdp_url = connect_to_gpm_profile(args.gpm_profile_id, args.gpm_api_base)
                print(f"[GPM] profile={args.gpm_profile_id} cdp={cdp_url}")
            else:
                playwright, _browser, context = connect_to_browser(args.cdp_url)
                print(f"[CDP] cdp={args.cdp_url}")
        except GPMLoginError as exc:
            if args.gpm_profile_id and not args.cdp_url_provided:
                print(f"[GPM ERROR] {exc}")
                print("[HINT] Open GPMLogin, enable/start its local API, then pass the correct API base:")
                print("[HINT] python main.py <url> --gpm-profile-id <id> --gpm-api-base http://127.0.0.1:<api_port>")
                print("[HINT] If the profile is already open, use --cdp-url http://127.0.0.1:<remote_debugging_port> instead.")
            else:
                print(f"[CDP ERROR] {exc}")
                print("[HINT] Start Chrome/GPMLogin with a live remote debugging port, then pass:")
                print("[HINT] python main.py <url> --cdp-url http://127.0.0.1:<remote_debugging_port>")
            return 1

        page = context.new_page()
        collection = get_collection()

        for url in args.urls:
            start = time.perf_counter()
            source_url = normalize_source_url(url)
            print(f"[START] {source_url}")
            try:
                comments = collect_from_video(
                    page,
                    url,
                    scroll_times=args.scroll_times,
                    max_reply_clicks=args.max_reply_clicks,
                    debug=args.debug,
                )
                saved = save_comments(comments, source_url, collection=collection, prune_stale=False)
                reply_count = sum(len(item.get("replies", [])) for item in comments)
                total_items = len(comments) + reply_count
                elapsed = time.perf_counter() - start
                print(
                    f"[OK] collected={len(comments)} replies={reply_count} total_items={total_items} "
                    f"saved={saved} elapsed={elapsed:.1f}s"
                )
            except Exception as exc:
                elapsed = time.perf_counter() - start
                print(f"[ERROR] {source_url} elapsed={elapsed:.1f}s error={exc}")

        return 0
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass
        if playwright:
            playwright.stop()


if __name__ == "__main__":
    raise SystemExit(main())
