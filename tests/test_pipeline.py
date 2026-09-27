"""Offline CSV integration tests with deterministic resolver responses."""

import csv
import json
from pathlib import Path
import tempfile
import unittest

from smilesearcher.models import Resolution
from smilesearcher.pipeline import run_pipeline


class FakeResolver:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.request_counts = {}

    def resolve(self, name):
        key = " ".join(name.casefold().split())
        self.calls.append(key)
        response = self.responses[key]
        if isinstance(response, BaseException):
            raise response
        return Resolution(name=name, **response)


class ForbiddenResolver:
    request_counts = {}

    def resolve(self, name):
        raise AssertionError("The offline pipeline must never invoke a resolver")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.input = self.directory / "input.csv"
        self.output = self.directory / "filled.csv"
        self.cache = self.directory / "cache.json"

    def write_rows(self, headers, rows):
        with self.input.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def write_cache(self, mapping):
        self.cache.write_text(json.dumps(mapping), encoding="utf-8")

    def read_rows(self, path=None):
        with (path or self.output).open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))

    def run_pipeline(self, resolver=None, **config):
        options = {"delay": 0, "retries": 0, "checkpoint_every": 1, **config}
        return run_pipeline(csv_path=self.input, cache_json=self.cache,
                            output_path=self.output, config=options, resolver=resolver)

    @staticmethod
    def found(smiles="CCO"):
        return {"smiles": smiles, "source": "test", "status": "found", "note": "", "trace": []}

    def test_existing_structures_and_literal_identifiers_are_preserved(self):
        self.write_rows(["identifier", "name", "smiles"], [
            {"identifier": "000012", "name": "ethanol", "smiles": "OCC"},
            {"identifier": "NA", "name": "benzene", "smiles": "c1ccccc1"},
        ])
        self.write_cache({"ethanol": "CCO", "benzene": "C1=CC=CC=C1"})
        self.run_pipeline(ForbiddenResolver(), offline=True)
        rows = self.read_rows()
        self.assertEqual([row["identifier"] for row in rows], ["000012", "NA"])
        self.assertEqual([row["smiles"] for row in rows], ["OCC", "c1ccccc1"])

    def test_offline_mode_fills_valid_cache_and_never_calls_resolver(self):
        self.write_rows(["canonical_name", "smiles"], [
            {"canonical_name": "ethanol", "smiles": ""},
            {"canonical_name": "broken", "smiles": ""},
            {"canonical_name": "uncached", "smiles": ""},
        ])
        self.write_cache({"ethanol": "CCO", "broken": "C1CC"})
        self.run_pipeline(ForbiddenResolver(), offline=True)
        self.assertEqual([row["smiles"] for row in self.read_rows()], ["CCO", "", ""])

    def test_conflicting_cache_names_remain_ambiguous_while_other_names_fill(self):
        self.write_rows(["name", "smiles"], [
            {"name": "ethanol", "smiles": ""}, {"name": "benzene", "smiles": ""},
        ])
        self.write_cache({"ethanol": "CCO", " ETHANOL ": "CO", "Ethanol": "CCO", "benzene": "c1ccccc1"})
        self.run_pipeline(ForbiddenResolver(), offline=True)
        self.assertEqual([row["smiles"] for row in self.read_rows()], ["", "c1ccccc1"])
        audit = self.read_rows(Path(str(self.output) + ".audit.csv"))
        self.assertEqual(audit[0]["status"], "ambiguous")
        self.assertEqual(audit[0]["source"], "cache_conflict")

    def test_invalid_existing_structure_is_retained_without_valid_replacement(self):
        self.write_rows(["name", "smiles"], [{"name": "unresolved reagent", "smiles": "C1CC"}])
        self.run_pipeline(ForbiddenResolver(), offline=True)
        self.assertEqual(self.read_rows()[0]["smiles"], "C1CC")
        audit = self.read_rows(Path(str(self.output) + ".audit.csv"))
        self.assertEqual(audit[0]["original_smiles"], "C1CC")
        self.assertEqual(audit[0]["smiles"], "C1CC")

    def test_invalid_existing_structure_can_be_replaced_by_valid_cache(self):
        self.write_rows(["name", "smiles"], [{"name": "ethanol", "smiles": "C1CC"}])
        self.write_cache({"ethanol": "CCO"})
        self.run_pipeline(ForbiddenResolver(), offline=True)
        self.assertEqual(self.read_rows()[0]["smiles"], "CCO")
        audit = self.read_rows(Path(str(self.output) + ".audit.csv"))
        self.assertEqual(audit[0]["original_smiles"], "C1CC")

    def test_normalized_duplicate_names_are_resolved_once(self):
        self.write_rows(["name", "smiles"], [
            {"name": " Ethanol ", "smiles": ""}, {"name": "ETHANOL", "smiles": ""},
        ])
        resolver = FakeResolver({"ethanol": self.found()})
        self.run_pipeline(resolver)
        self.assertEqual(resolver.calls, ["ethanol"])
        rows = self.read_rows()
        self.assertEqual([row["smiles"] for row in rows], ["CCO", "CCO"])
        self.assertEqual([row["name"] for row in rows], [" Ethanol ", "ETHANOL"])

    def test_provider_structure_is_validated_before_insertion(self):
        self.write_rows(["name", "smiles"], [{"name": "unresolved reagent", "smiles": ""}])
        resolver = FakeResolver({"unresolved reagent": self.found("C1CC")})
        self.run_pipeline(resolver)
        self.assertEqual(resolver.calls, ["unresolved reagent"])
        self.assertEqual(self.read_rows()[0]["smiles"], "")

    def test_transient_error_is_retried_on_a_later_run(self):
        self.write_rows(["name", "smiles"], [{"name": "ethanol", "smiles": ""}])
        first = FakeResolver({"ethanol": {
            "smiles": "", "status": "error", "source": "test", "note": "timeout", "trace": [],
        }})
        self.run_pipeline(first)
        self.assertEqual(self.read_rows()[0]["smiles"], "")
        second = FakeResolver({"ethanol": self.found()})
        self.run_pipeline(second)
        self.assertEqual(second.calls, ["ethanol"])
        self.assertEqual(self.read_rows()[0]["smiles"], "CCO")

    def test_not_found_is_skipped_until_requery_requested(self):
        self.write_rows(["name", "smiles"], [{"name": "ethanol", "smiles": ""}])
        first = FakeResolver({"ethanol": {
            "smiles": "", "status": "not_found", "source": "test", "note": "", "trace": [],
        }})
        self.run_pipeline(first)
        second = FakeResolver({"ethanol": self.found()})
        self.run_pipeline(second)
        self.assertEqual(second.calls, [])
        self.assertEqual(self.read_rows()[0]["smiles"], "")
        self.run_pipeline(second, requery_not_found=True)
        self.assertEqual(second.calls, ["ethanol"])
        self.assertEqual(self.read_rows()[0]["smiles"], "CCO")

    def test_mof_columns_are_detected_and_structure_columns_created(self):
        self.write_rows(["linker_1", "modulator_2", "metal_1", "sample"], [{
            "linker_1": "benzene", "modulator_2": "ethanol", "metal_1": "zinc", "sample": "007",
        }])
        self.write_cache({"benzene": "c1ccccc1", "ethanol": "CCO", "zinc": "[Zn+2]"})
        self.run_pipeline(ForbiddenResolver(), offline=True)
        row = self.read_rows()[0]
        self.assertEqual(row["linker_SMILES_1"], "c1ccccc1")
        self.assertEqual(row["modulator_SMILES_2"], "CCO")
        self.assertEqual(row["metal_SMILES_1"], "[Zn+2]")
        self.assertEqual(row["sample"], "007")

    def test_molecular_weights_use_preserved_and_validated_structures(self):
        self.write_rows(["name", "smiles"], [
            {"name": "ethanol", "smiles": "OCC"},
            {"name": "acetic acid", "smiles": ""},
            {"name": "unresolved reagent", "smiles": "C1CC"},
            {"name": "uncached reagent", "smiles": ""},
        ])
        self.write_cache({"acetic acid": "CC(=O)O"})
        stats = self.run_pipeline(ForbiddenResolver(), offline=True, molecular_weight=True)
        rows = self.read_rows()
        self.assertEqual([row["smiles"] for row in rows], ["OCC", "CC(=O)O", "C1CC", ""])
        self.assertAlmostEqual(float(rows[0]["molecular_weight_g_mol"]), 46.069, places=3)
        self.assertAlmostEqual(float(rows[1]["molecular_weight_g_mol"]), 60.052, places=3)
        self.assertEqual([row["molecular_weight_g_mol"] for row in rows[2:]], ["", ""])
        self.assertEqual(stats["molecular_weight_unit"], "g/mol")

    def test_mof_molecular_weight_columns_are_opt_in_and_match_structure_fields(self):
        self.write_rows(["linker_1", "linker_SMILES_1", "modulator_2", "metal_1"], [{
            "linker_1": "benzene", "linker_SMILES_1": "c1ccccc1",
            "modulator_2": "ethanol", "metal_1": "copper ion",
        }])
        self.write_cache({"ethanol": "CCO", "copper ion": "[Cu+2]"})
        self.run_pipeline(ForbiddenResolver(), offline=True)
        self.assertFalse(any("_MW_" in field for field in self.read_rows()[0]))
        stats = self.run_pipeline(ForbiddenResolver(), offline=True, molecular_weight=True)
        row = self.read_rows()[0]
        for field, expected in (("linker_MW_1", 78.114), ("modulator_MW_2", 46.069), ("metal_MW_1", 63.546)):
            self.assertAlmostEqual(float(row[field]), expected, places=3)
        self.assertEqual(stats["molecular_weight_columns"], {
            "linker_SMILES_1": "linker_MW_1", "modulator_SMILES_2": "modulator_MW_2", "metal_SMILES_1": "metal_MW_1",
        })

    def test_chemical_name_columns_are_detected(self):
        self.write_rows(["chemical_name", "smiles"], [{"chemical_name": "ethanol", "smiles": ""}])
        self.write_cache({"ethanol": "CCO"})
        self.run_pipeline(ForbiddenResolver(), offline=True)
        self.assertEqual(self.read_rows()[0]["smiles"], "CCO")

    def test_explicit_column_mapping_preserves_unrelated_fields(self):
        self.write_rows(["reagent", "structure", "name"], [{
            "reagent": "ethanol", "structure": "", "name": "NA",
        }])
        self.write_cache({"ethanol": "CCO"})
        self.run_pipeline(ForbiddenResolver(), offline=True, name_column="reagent", smiles_column="structure")
        self.assertEqual(self.read_rows()[0], {"reagent": "ethanol", "structure": "CCO", "name": "NA"})

    def test_audit_includes_named_and_structure_only_cells(self):
        self.write_rows(["name", "smiles"], [
            {"name": "ethanol", "smiles": ""},
            {"name": "", "smiles": "c1ccccc1"},
            {"name": "", "smiles": ""},
        ])
        self.write_cache({"ethanol": "CCO"})
        stats = self.run_pipeline(ForbiddenResolver(), offline=True)
        audit = self.read_rows(Path(str(self.output) + ".audit.csv"))
        self.assertEqual(len(audit), 2)
        self.assertTrue({"original_smiles", "smiles", "status", "source", "note"} <= set(audit[0]))
        summary = json.loads(Path(str(self.output) + ".summary.json").read_text(encoding="utf-8"))
        self.assertIsInstance(stats, dict)
        self.assertIsInstance(summary, dict)

    def test_stage_a_file_contains_only_offline_fills(self):
        self.write_rows(["name", "smiles"], [
            {"name": "ethanol", "smiles": ""}, {"name": "benzene", "smiles": ""},
        ])
        self.write_cache({"ethanol": "CCO"})
        stage_a = self.directory / "offline.csv"
        run_pipeline(csv_path=self.input, cache_json=self.cache, output_path=self.output,
                     stage_a_output_path=stage_a, config={"delay": 0, "retries": 0},
                     resolver=FakeResolver({"benzene": self.found("c1ccccc1")}))
        self.assertEqual([row["smiles"] for row in self.read_rows(stage_a)], ["CCO", ""])
        self.assertEqual([row["smiles"] for row in self.read_rows()], ["CCO", "c1ccccc1"])

    def test_resume_rejects_a_changed_input_file(self):
        self.write_rows(["name", "smiles"], [{"name": "ethanol", "smiles": ""}])
        self.run_pipeline(ForbiddenResolver(), offline=True)
        self.assertTrue(Path(str(self.output) + ".checkpoint.json").is_file())
        self.assertTrue(Path(str(self.output) + ".checkpoint.csv").is_file())
        self.write_rows(["name", "smiles"], [{"name": "benzene", "smiles": ""}])
        with self.assertRaises(ValueError):
            self.run_pipeline(ForbiddenResolver(), offline=True, resume=True)

    def test_resume_after_interruption_preserves_completed_rows_and_audit(self):
        self.write_rows(["name", "smiles"], [
            {"name": "ethanol", "smiles": ""}, {"name": "benzene", "smiles": ""},
        ])
        interrupted = FakeResolver({"ethanol": self.found(), "benzene": KeyboardInterrupt()})
        with self.assertRaises(KeyboardInterrupt):
            self.run_pipeline(interrupted)
        resumed = FakeResolver({"benzene": self.found("c1ccccc1")})
        self.run_pipeline(resumed, resume=True)
        self.assertEqual(resumed.calls, ["benzene"])
        self.assertEqual([row["smiles"] for row in self.read_rows()], ["CCO", "c1ccccc1"])
        audit = self.read_rows(Path(str(self.output) + ".audit.csv"))
        self.assertEqual(len(audit), 2)
        self.assertEqual([row["original_smiles"] for row in audit], ["", ""])

    def test_interrupted_multi_pair_row_replays_without_duplicate_audit(self):
        self.write_rows(["linker_1", "linker_SMILES_1", "linker_2", "linker_SMILES_2",
                         "modulator_1", "modulator_SMILES_1"], [{
            "linker_1": "ethanol", "linker_SMILES_1": "OCC",
            "linker_2": "benzene", "linker_SMILES_2": "C1CC",
            "modulator_1": "acetic acid", "modulator_SMILES_1": "",
        }])
        interrupted = FakeResolver({
            "benzene": self.found("c1ccccc1"), "acetic acid": KeyboardInterrupt(),
        })
        with self.assertRaises(KeyboardInterrupt):
            self.run_pipeline(interrupted)
        resumed = FakeResolver({"acetic acid": self.found("CC(=O)O")})
        self.run_pipeline(resumed, resume=True)
        self.assertEqual(resumed.calls, ["acetic acid"])
        row = self.read_rows()[0]
        self.assertEqual([row[field] for field in ("linker_SMILES_1", "linker_SMILES_2", "modulator_SMILES_1")],
                         ["OCC", "c1ccccc1", "CC(=O)O"])
        audit = self.read_rows(Path(str(self.output) + ".audit.csv"))
        self.assertEqual(len(audit), 3)
        self.assertEqual(len({(entry["row"], entry["smiles_column"]) for entry in audit}), 3)
        self.assertEqual([entry["original_smiles"] for entry in audit], ["OCC", "C1CC", ""])

    def test_blank_csv_headers_are_rejected(self):
        for header in ("name,,smiles", "name,   ,smiles"):
            with self.subTest(header=header):
                self.input.write_text(header + "\nethanol,007,\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.run_pipeline(ForbiddenResolver(), offline=True)
                self.assertFalse(self.output.exists())

    def test_case_insensitive_duplicate_csv_headers_are_rejected(self):
        self.input.write_text("Name,name,smiles\nethanol,benzene,\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_pipeline(ForbiddenResolver(), offline=True)
        self.assertFalse(self.output.exists())

    def test_output_cannot_overwrite_input(self):
        self.write_rows(["name", "smiles"], [{"name": "ethanol", "smiles": ""}])
        original = self.input.read_bytes()
        with self.assertRaises(ValueError):
            run_pipeline(csv_path=self.input, cache_json=self.cache, output_path=self.input,
                         config={"offline": True}, resolver=ForbiddenResolver())
        self.assertEqual(self.input.read_bytes(), original)

    def test_resume_rejects_changed_column_mapping(self):
        self.write_rows(["name", "other", "smiles"], [{"name": "ethanol", "other": "benzene", "smiles": ""}])
        self.run_pipeline(ForbiddenResolver(), offline=True, name_column="name", smiles_column="smiles")
        with self.assertRaises(ValueError):
            self.run_pipeline(ForbiddenResolver(), offline=True, resume=True,
                              name_column="other", smiles_column="smiles")


if __name__ == "__main__":
    unittest.main()
