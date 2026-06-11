from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection

from config.settings import settings


def get_collection() -> Collection:
    client = MongoClient(settings.mongo_url)
    collection = client[settings.mongo_db][settings.mongo_collection]
    collection.create_index(
        [("source_url", ASCENDING), ("name", ASCENDING), ("comment", ASCENDING)],
        unique=True,
        name="uniq_source_name_comment",
    )
    return collection


def get_telegram_subscriber_collection() -> Collection:
    client = MongoClient(settings.mongo_url)
    collection = client[settings.mongo_db][settings.telegram_subscriber_collection]
    collection.create_index("telegram_chat_id", unique=True, name="uniq_telegram_chat_id")
    return collection


def save_telegram_subscriber(message: dict, collection: Collection) -> None:
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        raise ValueError("Telegram message is missing chat.id")

    text = (message.get("text") or "").strip()
    command_parts = text.split(maxsplit=1)
    link_token = command_parts[1].strip() if len(command_parts) > 1 else ""
    now = datetime.now(timezone.utc)
    collection.update_one(
        {"telegram_chat_id": str(chat_id)},
        {
            "$set": {
                "telegram_chat_id": str(chat_id),
                "telegram_user_id": str(user.get("id", "")),
                "username": user.get("username", ""),
                "first_name": user.get("first_name", ""),
                "last_name": user.get("last_name", ""),
                "chat_type": chat.get("type", ""),
                "chat_title": chat.get("title", ""),
                "link_token": link_token,
                "active": True,
                "updated_at": now,
            },
            "$setOnInsert": {"started_at": now},
        },
        upsert=True,
    )


def deactivate_telegram_subscriber(chat_id: str | int, collection: Collection) -> None:
    collection.update_one(
        {"telegram_chat_id": str(chat_id)},
        {"$set": {"active": False, "updated_at": datetime.now(timezone.utc)}},
        upsert=False,
    )


def get_active_telegram_chat_ids(collection: Collection) -> list[str]:
    return [
        str(document["telegram_chat_id"])
        for document in collection.find({"active": True}, {"telegram_chat_id": 1})
        if document.get("telegram_chat_id") is not None
    ]


def save_comments(items: list[dict], source_url: str, collection: Collection | None = None, prune_stale: bool = True) -> int:
    target = collection if collection is not None else get_collection()
    saved = 0
    current_keys = []

    for item in items:
        query = {
            "source_url": source_url,
            "name": item.get("name", ""),
            "comment": item.get("comment", ""),
        }
        existing = target.find_one(query, {"replies": 1})
        replies = list(existing.get("replies", [])) if existing else []
        reply_keys = {(reply.get("name", ""), reply.get("comment", "")) for reply in replies}
        for reply in item.get("replies", []):
            reply_key = (reply.get("name", ""), reply.get("comment", ""))
            if reply_key in reply_keys:
                continue
            reply_keys.add(reply_key)
            replies.append(reply)

        document = {
            "platform": "tiktok",
            "source_url": source_url,
            "name": item.get("name", ""),
            "comment": item.get("comment", ""),
            "replies": replies,
            "collected_at": datetime.now(timezone.utc),
        }
        current_keys.append(
            {
                "source_url": document["source_url"],
                "name": document["name"],
                "comment": document["comment"],
            }
        )
        result = target.update_one(query, {"$set": document}, upsert=True)
        if result.upserted_id is not None or result.modified_count:
            saved += 1

    if prune_stale and current_keys:
        target.delete_many({"source_url": source_url, "$nor": current_keys})

    return saved
