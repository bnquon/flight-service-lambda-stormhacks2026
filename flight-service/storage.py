"""Save completed search records in MongoDB."""

import os

from pymongo import MongoClient


def save_search(record: dict) -> None:
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri or uri == "replace_me":
        return
    # Bound database calls so an unavailable cluster cannot consume the Lambda timeout.
    with MongoClient(uri, serverSelectionTimeoutMS=10000, timeoutMS=15000) as client:
        database = client[os.getenv("MONGODB_DATABASE", "flight_service")]
        collection = database[os.getenv("MONGODB_SEARCH_COLLECTION", "searches")]
        # Re-saving the same search replaces its document without creating duplicates.
        collection.replace_one(
            {"_id": record["search_id"]}, {"_id": record["search_id"], **record}, upsert=True,
        )
