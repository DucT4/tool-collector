import requests

from config.settings import settings


TELEGRAM_MESSAGE_LIMIT = 3900


class TelegramConfigError(RuntimeError):
    pass


def truncate_text(value: str, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def split_telegram_message(lines: list[str], limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    messages: list[str] = []
    current = ""

    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            messages.append(current)
        current = line[:limit]

    if current:
        messages.append(current)

    return messages


def format_comment_notifications(
    source_url: str,
    new_comments: list[dict],
    new_replies: list[dict],
) -> list[str]:
    lines = [
        "TikTok comments update",
        source_url,
        f"New comments: {len(new_comments)}",
        f"New replies: {len(new_replies)}",
    ]

    if new_comments:
        lines.append("")
        lines.append("Comments:")
        for item in new_comments:
            lines.append(f"- {truncate_text(item.get('name', ''))}: {truncate_text(item.get('comment', ''))}")

    if new_replies:
        lines.append("")
        lines.append("Replies:")
        for item in new_replies:
            parent = item.get("parent", {})
            reply = item.get("reply", {})
            lines.append(
                "- "
                f"{truncate_text(reply.get('name', ''))}: {truncate_text(reply.get('comment', ''))} "
                f"(to {truncate_text(parent.get('name', ''))}: {truncate_text(parent.get('comment', ''), 90)})"
            )

    return split_telegram_message(lines)


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = token if token is not None else settings.telegram_bot_token
        self.chat_id = chat_id if chat_id is not None else settings.telegram_chat_id

    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, text: str) -> None:
        if not self.enabled():
            raise TelegramConfigError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        response.raise_for_status()

    def send_messages(self, messages: list[str]) -> None:
        for message in messages:
            self.send_message(message)
