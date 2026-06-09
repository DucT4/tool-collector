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
