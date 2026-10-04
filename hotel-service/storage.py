"""Save completed search records in MongoDB."""

import os

from pymongo import MongoClient
from search_logging import log_event, log_step


def save_search(record: dict) -> None:
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri or uri == "replace_me":
        log_event("mongo.save", "skipped", session_id=record["session_id"],
                  search_id=record["search_id"], reason="MONGODB_URI not configured")
        return
    with log_step("mongo.save", session_id=record["session_id"], search_id=record["search_id"], status=record["status"]):
        # Bound database calls so an unavailable cluster cannot consume the Lambda timeout.
        with MongoClient(uri, serverSelectionTimeoutMS=10000, timeoutMS=15000) as client:
            database = client[os.getenv("MONGODB_DATABASE", "hotel_searches")]
            collection = database[os.getenv("MONGODB_SEARCH_COLLECTION", "searches")]
            # Re-saving the same search replaces its document without creating duplicates.
            collection.replace_one(
                {"_id": record["search_id"]}, {"_id": record["search_id"], **record}, upsert=True,
            )
