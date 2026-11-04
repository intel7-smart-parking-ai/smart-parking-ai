"""Shared catalog and cache views cannot be edited accidentally."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from chatbot import config
from chatbot.adapters import CachedAdapter, MockAdapter, request_gets
from chatbot.catalog import Catalog


class SnapshotTests(unittest.TestCase):
    def test_catalog_replaces_view_without_editing_old_view(self):
        catalog = Catalog(deepcopy(config.catalog.settings))
        before = catalog.lots()
        public = catalog.public_snapshot()
        lot = next(iter(before))
        with self.assertRaises(TypeError):
            before[lot]["spaces"].append("Z-99")
        with self.assertRaises(TypeError):
            public["lots"][0]["name"] = "changed"
        version = catalog.version
        catalog.register_space(lot, "Z-99")
        self.assertGreater(catalog.version, version)
        self.assertNotIn("Z-99", before[lot]["spaces"])
        self.assertIn("Z-99", catalog.lot(lot)["spaces"])

    def test_cache_and_fallback_share_readonly_rows(self):
        adapter = CachedAdapter(MockAdapter())
        token = request_gets.set({})
        self.addCleanup(request_gets.reset, token)
        result = adapter.get_empty_spots()
        self.assertIs(result, adapter.cache["empty"])
        lot = next(iter(result["lots"]))
        with self.assertRaises(TypeError):
            result["lots"][lot]["empty"] = -1
        with patch.object(adapter.inner, "get_empty_spots", side_effect=OSError):
            fallback = adapter.get_empty_spots()
        self.assertTrue(fallback["stale"])
        self.assertNotIn("stale", result)
        self.assertIs(fallback["lots"], result["lots"])
