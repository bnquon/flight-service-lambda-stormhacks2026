"""Offline Mongo persistence contract tests."""

from copy import deepcopy
import os
import unittest
from unittest.mock import patch

import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        client = patch("storage.MongoClient")
        self.client = client.start()
        self.addCleanup(client.stop)
        self.connection = self.client.return_value.__enter__.return_value
        self.record = {
            "search_id": "search-123",
            "session_id": "session-123",
            "status": "complete",
            "flights": [{"price": 1324, "currency": "CAD"}],
        }

    def test_missing_uri_skips_mongo(self):
        storage.save_search(self.record)
        self.client.assert_not_called()

    def test_placeholder_uri_skips_mongo(self):
        os.environ["MONGODB_URI"] = "replace_me"
        storage.save_search(self.record)
        self.client.assert_not_called()

    def test_default_database_and_collection_upsert_record(self):
        os.environ["MONGODB_URI"] = "mongodb://example.test"
        original = deepcopy(self.record)

        storage.save_search(self.record)

        self.client.assert_called_once_with(
            "mongodb://example.test",
            serverSelectionTimeoutMS=10000,
            timeoutMS=15000,
        )
        self.connection.__getitem__.assert_called_once_with("flight_service")
        database = self.connection.__getitem__.return_value
        database.__getitem__.assert_called_once_with("searches")
        database.__getitem__.return_value.replace_one.assert_called_once_with(
            {"_id": "search-123"},
            {"_id": "search-123", **self.record},
            upsert=True,
        )
        self.assertEqual(self.record, original)
        self.client.return_value.__exit__.assert_called_once_with(None, None, None)

    def test_configured_database_and_collection(self):
        os.environ.update({
            "MONGODB_URI": "mongodb://example.test",
            "MONGODB_DATABASE": "custom_database",
            "MONGODB_SEARCH_COLLECTION": "custom_searches",
        })

        storage.save_search(self.record)

        self.connection.__getitem__.assert_called_once_with("custom_database")
        database = self.connection.__getitem__.return_value
        database.__getitem__.assert_called_once_with("custom_searches")

    def test_write_errors_propagate(self):
        os.environ["MONGODB_URI"] = "mongodb://example.test"
        collection = self.connection.__getitem__.return_value.__getitem__.return_value
        collection.replace_one.side_effect = RuntimeError("write failed")

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            storage.save_search(self.record)

    def test_connection_errors_propagate(self):
        os.environ["MONGODB_URI"] = "mongodb://example.test"
        self.client.side_effect = RuntimeError("connection failed")

        with self.assertRaisesRegex(RuntimeError, "connection failed"):
            storage.save_search(self.record)


if __name__ == "__main__":
    unittest.main()
