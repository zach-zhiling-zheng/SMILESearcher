# SPDX-License-Identifier: GPL-3.0-only
# Modified 2026-09-27 by Zheng Lab.
import unittest
from unittest.mock import Mock, patch

import requests

from smilesearcher.chemistry import canonicalize_smiles
from smilesearcher.resolvers import Resolver


def response(status=200, data=None, text="", headers=None):
    result = Mock(status_code=status, text=text, headers=headers or {})
    result.json.return_value = data
    return result


def pubchem(*smiles, property_name="SMILES"):
    return response(data={"PropertyTable": {"Properties": [
        {"CID": index, property_name: item} for index, item in enumerate(smiles, 1)
    ]}})


def session_for(*results):
    session = Mock()
    session.headers = {}
    session.get.side_effect = results
    return session


class ResolverTests(unittest.TestCase):
    def resolver(self, session, providers=("pubchem",), **kwargs):
        return Resolver(providers=providers, session=session, delay=0, **kwargs)

    def test_modern_pubchem_preserves_chirality_and_name(self):
        session = session_for(pubchem("C[C@H](O)C(=O)O"))
        result = self.resolver(session).resolve(" (S)-lactic acid ")
        self.assertEqual(result.status, "found")
        self.assertIn("@", result.smiles)
        self.assertIn("%28S%29-lactic%20acid/property/SMILES/JSON", session.get.call_args.args[0])
        self.assertEqual(result.name, "(S)-lactic acid")

    def test_legacy_isomeric_smiles_json(self):
        result = self.resolver(session_for(pubchem("CCO", property_name="IsomericSMILES"))).resolve("ethanol")
        self.assertEqual(result.smiles, "CCO")

    def test_connectivity_only_response_is_not_treated_as_isomeric(self):
        for property_name in ("ConnectivitySMILES", "CanonicalSMILES"):
            result = self.resolver(session_for(pubchem("CC(O)C(=O)O", property_name=property_name))).resolve("lactic acid")
            self.assertEqual(result.status, "error")
            self.assertEqual(result.smiles, "")

    def test_distinct_isomers_stop_resolution_as_ambiguous(self):
        session = session_for(pubchem("C[C@H](O)C(=O)O", "C[C@@H](O)C(=O)O"))
        result = self.resolver(session, ("pubchem", "opsin")).resolve("lactic acid")
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.smiles, "")
        self.assertEqual(session.get.call_count, 1)

    def test_equivalent_returned_structures_are_not_ambiguous(self):
        result = self.resolver(session_for(pubchem("CCO", "OCC"))).resolve("ethanol")
        self.assertEqual(result.status, "found")

    def test_invalid_response_is_not_silently_discarded_among_valid_hits(self):
        result = self.resolver(session_for(pubchem("CCO", "invalid"))).resolve("ethanol")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.smiles, "")

    def test_fallback_order_after_nohit(self):
        session = session_for(response(404), response(404), response(data={"status": "SUCCESS", "smiles": "CCO"}))
        result = self.resolver(session, ("pubchem", "cactus", "opsin")).resolve("ethanol")
        self.assertEqual(result.source, "opsin")
        self.assertEqual([item["provider"] for item in result.trace], ["pubchem", "cactus", "opsin"])
        self.assertEqual([item["requests"] for item in result.trace], [1, 1, 1])

    def test_network_error_is_distinguished_from_nohit(self):
        session = session_for(requests.Timeout(), response(404))
        result = self.resolver(session, ("pubchem", "opsin"), retries=0).resolve("ethanol")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.source, "pubchem")
        self.assertIn("Timeout", result.note)
        self.assertEqual(len(result.trace), 2)

    @patch("smilesearcher.resolvers.time.sleep")
    def test_retry_counts_against_budget(self, sleep):
        session = session_for(response(503), pubchem("CCO"))
        resolver = self.resolver(session, budgets={"pubchem": 2})
        result = resolver.resolve("ethanol")
        self.assertEqual(result.status, "found")
        self.assertEqual(result.trace[0]["requests"], 2)
        self.assertEqual(resolver.request_counts["pubchem"], 2)
        self.assertEqual(resolver.resolve("methanol").status, "budget_exhausted")
        self.assertEqual(session.get.call_count, 2)

    @patch("smilesearcher.resolvers.time.sleep")
    def test_budget_can_prevent_retry(self, sleep):
        session = session_for(response(503))
        result = self.resolver(session, budgets={"pubchem": 1}).resolve("ethanol")
        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(session.get.call_count, 1)

    def test_zero_budget_sends_no_requests(self):
        session = session_for()
        result = self.resolver(session, budgets={"pubchem": 0}).resolve("ethanol")
        self.assertEqual(result.status, "budget_exhausted")
        session.get.assert_not_called()

    @patch("smilesearcher.resolvers.time.sleep")
    def test_retry_after_is_respected(self, sleep):
        session = session_for(response(429, headers={"Retry-After": "5"}), pubchem("CCO"))
        result = self.resolver(session).resolve("ethanol")
        self.assertEqual(result.status, "found")
        sleep.assert_any_call(5.0)

    @patch("smilesearcher.resolvers.time.sleep")
    def test_long_retry_after_defers_without_hammering_provider(self, sleep):
        session = session_for(response(429, headers={"Retry-After": "600"}))
        resolver = self.resolver(session)
        self.assertEqual(resolver.resolve("ethanol").status, "blocked")
        self.assertEqual(resolver.resolve("methanol").status, "blocked")
        session.get.assert_called_once()
        sleep.assert_not_called()

    def test_forbidden_is_not_retried_or_bypassed(self):
        session = session_for(response(403))
        resolver = self.resolver(session)
        self.assertEqual(resolver.resolve("ethanol").status, "blocked")
        self.assertEqual(resolver.resolve("methanol").status, "blocked")
        self.assertEqual(session.get.call_count, 1)

    @patch("smilesearcher.resolvers.time.sleep")
    def test_network_retry_is_bounded(self, sleep):
        session = session_for(requests.Timeout(), requests.Timeout(), requests.Timeout())
        result = self.resolver(session, retries=2).resolve("ethanol")
        self.assertEqual(result.status, "error")
        self.assertEqual(session.get.call_count, 3)

    def test_malformed_json_is_error(self):
        bad = response()
        bad.json.side_effect = ValueError("invalid JSON")
        self.assertEqual(self.resolver(session_for(bad)).resolve("ethanol").status, "error")

    def test_cactus_reports_all_structures(self):
        result = self.resolver(session_for(response(text="CCO\nCCCO\n")), ("cactus",)).resolve("alcohol")
        self.assertEqual(result.status, "ambiguous")

    def test_cactus_html_is_not_a_structure(self):
        result = self.resolver(session_for(response(text="<html>not found</html>")), ("cactus",)).resolve("ethanol")
        self.assertEqual(result.status, "error")

    def test_cactus_dative_bond_is_a_valid_structure(self):
        result = self.resolver(session_for(response(text="N->[Cu+2]")), ("cactus",)).resolve("copper ammine")
        self.assertEqual(result.status, "found")
        self.assertIn("->", result.smiles)

    def test_cactus_omitted_stereochemistry_falls_through_to_opsin(self):
        session = session_for(response(text="CC(O)C(=O)O"),
                              response(data={"status": "SUCCESS", "smiles": "C[C@H](O)C(=O)O"}))
        result = self.resolver(session, ("cactus", "opsin")).resolve("(S)-lactic acid")
        self.assertEqual(result.status, "found")
        self.assertEqual(result.source, "opsin")
        self.assertEqual(result.trace[0]["status"], "error")
        self.assertIn("https://www.ebi.ac.uk/opsin/ws/", session.get.call_args.args[0])

    def test_opsin_warning_requires_review(self):
        result = self.resolver(session_for(response(data={"status": "WARNING", "smiles": "CCO"})), ("opsin",)).resolve("ethanol")
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.smiles, "")

    def test_opsin_failure_is_nohit(self):
        result = self.resolver(session_for(response(data={"status": "FAILURE"})), ("opsin",)).resolve("unknown name")
        self.assertEqual(result.status, "not_found")

    def test_no_automatic_removal_of_hydrate_or_parenthetical_identity(self):
        session = session_for(response(404))
        self.resolver(session).resolve("copper(II) sulfate pentahydrate (98%)")
        url = session.get.call_args.args[0]
        self.assertIn("copper%28II%29%20sulfate%20pentahydrate%20%2898%25%29", url)

    def test_missing_name_sends_no_requests(self):
        session = session_for()
        self.assertEqual(self.resolver(session).resolve(None).status, "missing_name")
        session.get.assert_not_called()

    @staticmethod
    def wiki_responses(label="ethanol", alias=None, smiles="CCO", prop="P2017"):
        page = response(data={"query": {"pages": {"1": {"title": "Ethanol", "pageprops": {"wikibase_item": "Q1"}}}}})
        entity = response(data={"entities": {"Q1": {
            "labels": {"en": {"value": label}},
            "aliases": {"en": [{"value": alias}] if alias else []},
            "claims": {prop: [{"mainsnak": {"datavalue": {"value": smiles}}}]},
        }}})
        return page, entity

    def test_wikipedia_requires_exact_entity_name_or_alias(self):
        session = session_for(*self.wiki_responses(label="methanol"))
        result = self.resolver(session, ("wikipedia",)).resolve("ethanol")
        self.assertEqual(result.status, "not_found")
        self.assertNotIn("list", session.get.call_args_list[0].kwargs["params"])

    def test_wikipedia_exact_alias_with_isomeric_property(self):
        session = session_for(*self.wiki_responses(label="ethanol", alias="ethyl alcohol"))
        result = self.resolver(session, ("wikipedia",)).resolve("ethyl alcohol")
        self.assertEqual(result.smiles, "CCO")
        self.assertEqual(result.trace[0]["requests"], 2)

    def test_wikipedia_connectivity_property_is_insufficient(self):
        session = session_for(*self.wiki_responses(prop="P233"))
        self.assertEqual(self.resolver(session, ("wikipedia",)).resolve("ethanol").status, "not_found")

    def test_configuration_validation(self):
        for kwargs in ({"providers": ["google"]}, {"providers": []}, {"timeout": 0},
                       {"delay": -1}, {"retries": -1}, {"budgets": {"pubchem": -1}}):
            with self.assertRaises(ValueError):
                Resolver(**kwargs)

    def test_injected_session_is_not_closed(self):
        session = session_for()
        with self.resolver(session):
            pass
        session.close.assert_not_called()

    @patch("smilesearcher.resolvers.requests.Session")
    def test_owned_session_is_closed(self, session_class):
        with Resolver():
            pass
        session_class.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
