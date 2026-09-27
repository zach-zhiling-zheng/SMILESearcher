"""Cache persistence and validation checks using temporary files."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from smilesearcher.cache import get_cached_smiles, load_cache_json, save_cache_json


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cache.json"

    def write_json(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_missing_cache_starts_empty(self):
        self.assertEqual(load_cache_json(self.path), {})

    def test_legacy_strings_use_case_and_whitespace_independent_keys(self):
        self.write_json({"  Ethanol  ": "CCO", "acetic   acid": "CC(=O)O"})
        cache = load_cache_json(self.path)
        self.assertEqual(get_cached_smiles(cache, "ETHANOL"), "CCO")
        self.assertEqual(get_cached_smiles(cache, " Acetic ACID "), "CC(=O)O")

    def test_invalid_cached_structure_is_not_returned(self):
        self.write_json({"broken ring": "C1CC", "nonchemical": "not a structure"})
        cache = load_cache_json(self.path)
        self.assertEqual(get_cached_smiles(cache, "broken ring"), "")
        self.assertEqual(get_cached_smiles(cache, "nonchemical"), "")

    def test_conflicting_case_insensitive_cache_entries_are_rejected(self):
        self.write_json({"ethanol": "CCO", " ETHANOL ": "CO"})
        with self.assertRaises(ValueError):
            load_cache_json(self.path)

    def test_unsuccessful_status_does_not_supply_a_structure(self):
        self.write_json({
            "ethanol": {"smiles": "CCO", "status": "error", "source": "pubchem"},
            "acetate": {"smiles": "CC(=O)O", "status": "not_found"},
        })
        cache = load_cache_json(self.path)
        self.assertEqual(get_cached_smiles(cache, "ethanol"), "")
        self.assertEqual(get_cached_smiles(cache, "acetate"), "")

    def test_rich_record_provenance_survives_round_trip(self):
        self.write_json({"ethanol": {
            "name": "Ethanol", "smiles": "CCO", "status": "found",
            "source": "pubchem", "note": "CID 702", "trace": ["pubchem: found"],
        }})
        cache = load_cache_json(self.path)
        save_cache_json(self.path, cache)
        loaded = load_cache_json(self.path)
        self.assertEqual(get_cached_smiles(loaded, "ethanol"), "CCO")
        for key in ("source", "note", "trace", "status"):
            self.assertEqual(loaded["ethanol"][key], cache["ethanol"][key])

    def test_empty_lowercase_structure_falls_back_to_legacy_uppercase(self):
        self.write_json({"ethanol": {"smiles": "", "SMILES": "CCO", "source": "legacy"}})
        cache = load_cache_json(self.path)
        self.assertEqual(get_cached_smiles(cache, "ethanol"), "CCO")
        self.assertEqual(cache["ethanol"]["status"], "found")
        self.assertEqual(cache["ethanol"]["source"], "legacy")

    def test_corrupt_or_wrong_root_json_raises_instead_of_losing_cache(self):
        for content in ("{unclosed", "[]", '"CCO"'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_cache_json(self.path)
                self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_failed_atomic_replace_keeps_previous_cache(self):
        self.write_json({"ethanol": "CCO"})
        original = self.path.read_bytes()
        cache = load_cache_json(self.path)
        cache["benzene"] = {
            "name": "benzene", "smiles": "c1ccccc1", "status": "found", "source": "test",
        }
        with mock.patch("os.replace", side_effect=OSError("simulated full disk")):
            with self.assertRaises(OSError):
                save_cache_json(self.path, cache)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
