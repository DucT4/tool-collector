import truststore
import requests

from config.settings import settings
from database.mongo import deactivate_telegram_subscriber, save_telegram_subscriber


truststore.inject_into_ssl()


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
        return bool(self.token)

    def send_message(self, text: str, chat_id: str | int | None = None) -> None:
        target_chat_id = str(chat_id) if chat_id is not None else self.chat_id
        if not self.token:
            raise TelegramConfigError("Missing TELEGRAM_BOT_TOKEN")
        if not target_chat_id:
            raise TelegramConfigError("Missing Telegram chat id")

        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": target_chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        response.raise_for_status()

    def send_messages(self, messages: list[str], chat_id: str | int | None = None) -> None:
        for message in messages:
            self.send_message(message, chat_id=chat_id)

    def get_updates(self, offset: int | None = None) -> list[dict]:
        if not self.token:
            raise TelegramConfigError("Missing TELEGRAM_BOT_TOKEN")
        params: dict[str, object] = {"timeout": 0, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        response = requests.get(
            f"https://api.telegram.org/bot{self.token}/getUpdates",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "Telegram getUpdates failed"))
        return payload.get("result", [])


class TelegramSubscriptionService:
    def __init__(self, notifier: TelegramNotifier, collection) -> None:
        self.notifier = notifier
        self.collection = collection
        self.next_offset: int | None = None

    @staticmethod
    def _command(text: str) -> str:
        first = (text or "").strip().split(maxsplit=1)[0].lower()
        return first.split("@", 1)[0]

    def poll_once(self) -> int:
        updates = self.notifier.get_updates(offset=self.next_offset)
        processed = 0

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self.next_offset = max(self.next_offset or 0, update_id + 1)

            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            command = self._command(message.get("text", ""))
            if chat_id is None or command not in ("/start", "/stop"):
                continue

            if command == "/start":
                save_telegram_subscriber(message, self.collection)
                self.notifier.send_message(
                    "Da dang ky nhan thong bao TikTok. Gui /stop de ngung nhan thong bao.",
                    chat_id=chat_id,
                )
            else:
                deactivate_telegram_subscriber(chat_id, self.collection)
                self.notifier.send_message("Da ngung nhan thong bao TikTok. Gui /start de dang ky lai.", chat_id=chat_id)
            processed += 1

        return processed
