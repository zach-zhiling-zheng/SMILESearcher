# SPDX-License-Identifier: GPL-3.0-only
# Modified 2026-09-27 by Zheng Lab.
import unittest
import tempfile
from pathlib import Path

from smilesearcher.cache import get_cached_smiles, load_cache_json, save_cache_json
from smilesearcher.chemistry import canonicalize_smiles, is_missing, molecular_weight, name_key, normalize_name
from smilesearcher.models import Resolution


class ChemistryTests(unittest.TestCase):
    def test_missing_values_do_not_treat_no_as_missing(self):
        for value in (None, float("nan"), "", " nan ", "NULL", "not reported"):
            self.assertTrue(is_missing(value))
        for value in ("Na", "na", "NO", "no", "water", "[Na+]", 0):
            self.assertFalse(is_missing(value))

    def test_normalization_retains_chemical_descriptors(self):
        name = "  copper(II)  sulfate\n pentahydrate (98%) "
        self.assertEqual(normalize_name(name), "copper(II) sulfate pentahydrate (98%)")
        self.assertEqual(name_key(" (R)-Lactic\tAcid "), "(r)-lactic acid")

    def test_formula_keys_keep_element_case_and_are_idempotent(self):
        self.assertNotEqual(name_key("CO"), name_key("Co"))
        self.assertNotEqual(name_key("NO"), name_key("No"))
        self.assertNotEqual(name_key("Si"), name_key("SI"))
        for name in ("CO", "Co", "NO", "Na", "FeCl3", "H2O", "ethanol", "DMF"):
            self.assertEqual(name_key(name_key(name)), name_key(name))
        self.assertEqual(name_key("DMF"), "dmf")

    def test_formula_cache_roundtrip_preserves_sodium_and_distinct_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cache.json"
            originals = {"Na": "[Na+]", "CO": "[C-]#[O+]", "Co": "[Co]"}
            save_cache_json(path, {name_key(name): {
                "name": name, "smiles": smiles, "status": "found"
            } for name, smiles in originals.items()})
            loaded = load_cache_json(path)
            self.assertEqual(len(loaded), 3)
            for name, smiles in originals.items():
                self.assertEqual(get_cached_smiles(loaded, name), canonicalize_smiles(smiles))

    def test_legacy_formula_cache_keys_remain_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cache.json"
            save_cache_json(path, {"CO": "[C-]#[O+]", "Co": "[Co]"})
            loaded = load_cache_json(path)
            self.assertNotEqual(get_cached_smiles(loaded, "CO"), get_cached_smiles(loaded, "Co"))

    def test_canonical_equivalence(self):
        self.assertEqual(canonicalize_smiles("OCC"), canonicalize_smiles("CCO"))

    def test_stereoisomers_remain_distinct(self):
        left = canonicalize_smiles("C[C@H](O)C(=O)O")
        right = canonicalize_smiles("C[C@@H](O)C(=O)O")
        self.assertIsNotNone(left)
        self.assertNotEqual(left, right)
        self.assertIn("@", left)

    def test_isotopes_and_salt_components_are_retained(self):
        result = canonicalize_smiles("[13CH3]CO.[Na+].[Cl-]")
        self.assertIn("13C", result)
        self.assertIn("[Na+]", result)
        self.assertIn("[Cl-]", result)
        self.assertEqual(result.count("."), 2)

    def test_invalid_and_trailing_text_are_rejected(self):
        for value in ("invalid_smiles", "C1CC", "CCO ethanol", "CCO\nCC", "", "<html>"):
            self.assertIsNone(canonicalize_smiles(value))

    def test_molecular_weight_of_ethanol(self):
        self.assertAlmostEqual(molecular_weight("CCO"), 46.069, places=3)
        self.assertAlmostEqual(molecular_weight("OCC"), 46.069, places=3)

    def test_molecular_weight_of_terephthalic_acid(self):
        self.assertAlmostEqual(molecular_weight("O=C(O)c1ccc(C(=O)O)cc1"), 166.132, places=3)

    def test_molecular_weight_includes_salt_and_hydration_fragments(self):
        salt = molecular_weight("[Na+].[Cl-]")
        water = molecular_weight("O")
        hydrated = molecular_weight("[Na+].[Cl-].O.O")
        self.assertAlmostEqual(hydrated, salt + 2 * water, places=6)
        self.assertAlmostEqual(hydrated, 94.473, delta=0.01)

    def test_molecular_weight_uses_explicit_isotope_mass(self):
        difference = molecular_weight("[13CH3]CO") - molecular_weight("CCO")
        self.assertAlmostEqual(difference, 0.992355, places=5)

    def test_molecular_weight_rejects_invalid_or_unspecified_structures(self):
        for smiles in (None, "", "invalid_smiles", "C1CC", "CCO ethanol", "*", "CC*"):
            self.assertIsNone(molecular_weight(smiles))

    def test_resolution_dict_has_independent_trace(self):
        result = Resolution("ethanol", "CCO", "pubchem", "found")
        result.trace.append({"provider": "pubchem"})
        data = result.to_dict()
        data["trace"].clear()
        self.assertEqual(len(result.trace), 1)
        self.assertEqual(Resolution("methanol").trace, [])


if __name__ == "__main__":
    unittest.main()
