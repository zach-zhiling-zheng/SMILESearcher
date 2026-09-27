# SPDX-License-Identifier: GPL-3.0-only
# Modified 2026-09-27 by Zheng Lab; adapted from StarLiu714/SMILESearcher
# and the local building-block name-resolution workflow.
"""Validated name resolution through public chemical services."""

from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re
import time
from urllib.parse import quote

import requests

from .chemistry import canonicalize_smiles, name_key, normalize_name
from .models import Resolution


PROVIDERS = ("pubchem", "cactus", "opsin", "wikipedia")
_NAMED_STEREO = re.compile(r"\((?:\d*[RSEZ](?:,\d*[RSEZ])*)\)|(?:^|\W)[DL]-|\b(?:cis|trans)-")


class _ProviderFailure(Exception):
    def __init__(self, status, note):
        self.status = status
        self.note = note
        super().__init__(note)


class Resolver:
    """Try providers in order; budgets count actual HTTP attempts, including retries.

    One Resolver is intended for sequential use. Provider budgets apply for its
    lifetime; Wikipedia can use several HTTP requests for one name.
    """

    def __init__(self, providers=("pubchem", "cactus", "opsin"), timeout=20,
                 retries=2, delay=0.3, budgets=None, session=None):
        if isinstance(providers, str):
            providers = (providers,)
        self.providers = tuple(dict.fromkeys(providers))
        if not self.providers or any(p not in PROVIDERS for p in self.providers):
            raise ValueError("Choose one or more of: " + ", ".join(PROVIDERS))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
            raise ValueError("retries must be a nonnegative integer")
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("delay must be between 0 and 60 seconds")
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self.budgets = dict(budgets or {})
        if any(p not in PROVIDERS or not isinstance(v, int) or isinstance(v, bool)
               or v < 0 for p, v in self.budgets.items()):
            raise ValueError("budgets must map provider names to nonnegative integers")
        self.request_counts = Counter()
        self.session = session if session is not None else requests.Session()
        self.session.headers.update({
            "User-Agent": "SMILESearcher/0.2 (https://github.com/zach-zhiling-zheng/SMILESearcher)",
            "Accept": "application/json,text/plain;q=0.9",
        })
        self._owns_session = session is None
        self._last_request = None
        self._blocked = set()
        self._deferred_until = {}

    def close(self):
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @staticmethod
    def _retry_after(response):
        value = response.headers.get("Retry-After", "")
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                instant = parsedate_to_datetime(value)
                if instant.tzinfo is None:
                    instant = instant.replace(tzinfo=timezone.utc)
                seconds = (instant - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return 0.0
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0

    def _request(self, provider, url, **kwargs):
        if provider in self._blocked:
            raise _ProviderFailure("blocked", "Provider access was denied in this session.")
        if time.monotonic() < self._deferred_until.get(provider, 0):
            raise _ProviderFailure("blocked", "Provider Retry-After interval has not elapsed.")
        for attempt in range(self.retries + 1):
            if self.request_counts[provider] >= self.budgets.get(provider, math.inf):
                raise _ProviderFailure("budget_exhausted", "HTTP request budget exhausted.")
            if self._last_request is not None:
                remaining = self.delay - (time.monotonic() - self._last_request)
                if remaining > 0:
                    time.sleep(remaining)
            self.request_counts[provider] += 1
            self._last_request = time.monotonic()
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=False, **kwargs)
            except requests.RequestException as exc:
                if attempt < self.retries:
                    time.sleep(min(0.5 * 2 ** attempt, 30))
                    continue
                raise _ProviderFailure("error", f"Network request failed ({type(exc).__name__}).") from exc
            code = response.status_code
            if code == 403:
                self._blocked.add(provider)
                raise _ProviderFailure("blocked", "Provider returned HTTP 403; no further requests attempted.")
            if code == 429 or code in (500, 502, 503, 504):
                pause = max(self._retry_after(response), min(0.5 * 2 ** attempt, 30))
                if pause > 60 or attempt == self.retries:
                    if code == 429 or pause > 60:
                        self._deferred_until[provider] = time.monotonic() + pause
                    status = "blocked" if code == 429 else "error"
                    raise _ProviderFailure(status, f"Provider returned HTTP {code}; retry deferred.")
                time.sleep(pause)
                continue
            return response
        raise AssertionError("Unreachable request state")

    def _json(self, provider, url, **kwargs):
        response = self._request(provider, url, **kwargs)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise _ProviderFailure("error", f"Provider returned HTTP {response.status_code}.")
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise _ProviderFailure("error", "Provider returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise _ProviderFailure("error", "Provider returned an unexpected JSON structure.")
        return data

    @staticmethod
    def _select(candidates):
        if not candidates:
            return "", "not_found", "No structure returned."
        canonical = [canonicalize_smiles(item) for item in candidates]
        if any(item is None for item in canonical):
            return "", "error", "Provider returned an invalid SMILES structure."
        structures = set(canonical)
        if len(structures) > 1:
            return "", "ambiguous", "Multiple distinct structures returned; review required."
        return structures.pop(), "found", ""

    def _pubchem(self, name):
        encoded = quote(name, safe="")
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}/property/SMILES/JSON"
        data = self._json("pubchem", url, params={"name_type": "complete"})
        if data is None:
            return [], ""
        properties = data.get("PropertyTable", {}).get("Properties")
        if not isinstance(properties, list):
            raise _ProviderFailure("error", "PubChem response has no property table.")
        candidates = []
        for item in properties:
            if not isinstance(item, dict):
                raise _ProviderFailure("error", "PubChem returned an invalid property row.")
            value = item.get("SMILES") or item.get("IsomericSMILES")
            if not value:
                # ConnectivitySMILES / CanonicalSMILES omit stereochemistry.
                raise _ProviderFailure("error", "PubChem response lacks stereochemistry-capable SMILES.")
            candidates.append(value)
        return candidates, ""

    def _cactus(self, name):
        url = f"https://cactus.nci.nih.gov/chemical/structure/{quote(name, safe='')}/smiles"
        response = self._request("cactus", url)
        if response.status_code == 404:
            return [], ""
        if response.status_code != 200:
            raise _ProviderFailure("error", f"Provider returned HTTP {response.status_code}.")
        lines = [line.strip() for line in response.text.splitlines() if line.strip()]
        if any(line.startswith("<") for line in lines):
            raise _ProviderFailure("error", "CACTUS returned a non-structure response.")
        if _NAMED_STEREO.search(name) and any(not any(mark in line for mark in "@/\\") for line in lines):
            raise _ProviderFailure("error", "CACTUS omitted explicitly named stereochemistry.")
        return lines, ""

    def _opsin(self, name):
        url = f"https://www.ebi.ac.uk/opsin/ws/{quote(name, safe='')}.json"
        data = self._json("opsin", url)
        if data is None or str(data.get("status", "")).upper() == "FAILURE":
            return [], ""
        status = str(data.get("status", "")).upper()
        if status == "WARNING":
            raise _ProviderFailure("ambiguous", "OPSIN returned parsing warnings; review required.")
        if status not in ("SUCCESS", "OK") or not data.get("smiles"):
            raise _ProviderFailure("error", "OPSIN returned an unexpected result.")
        return [data["smiles"]], ""

    def _wikipedia(self, name):
        data = self._json("wikipedia", "https://en.wikipedia.org/w/api.php", params={
            "action": "query", "format": "json", "titles": name,
            "prop": "pageprops", "redirects": 1,
        })
        pages = (data or {}).get("query", {}).get("pages", {})
        if not isinstance(pages, dict):
            raise _ProviderFailure("error", "Wikipedia returned an unexpected page response.")
        identifiers = []
        for page in pages.values():
            properties = page.get("pageprops", {})
            if "missing" in page or "disambiguation" in properties:
                continue
            if properties.get("wikibase_item"):
                identifiers.append(properties["wikibase_item"])
        if not identifiers:
            return [], ""
        data = self._json("wikipedia", "https://www.wikidata.org/w/api.php", params={
            "action": "wbgetentities", "format": "json", "ids": "|".join(identifiers),
            "props": "labels|aliases|claims", "languages": "en",
        })
        candidates = []
        for entity in (data or {}).get("entities", {}).values():
            names = [entity.get("labels", {}).get("en", {}).get("value", "")]
            names.extend(a.get("value", "") for a in entity.get("aliases", {}).get("en", []))
            if name_key(name) not in {name_key(item) for item in names}:
                continue
            # P2017 is isomeric SMILES; P233 connectivity-only is insufficient.
            for claim in entity.get("claims", {}).get("P2017", []):
                if claim.get("rank") == "deprecated":
                    continue
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                if isinstance(value, str):
                    candidates.append(value)
        return candidates, ""

    def resolve(self, name) -> Resolution:
        normalized = normalize_name(name)
        if not normalized:
            return Resolution(normalized, status="missing_name", note="No chemical name supplied.")
        trace = []
        priority = {"not_found": 0, "budget_exhausted": 1, "error": 2, "blocked": 3, "ambiguous": 4}
        final = Resolution(normalized)
        for provider in self.providers:
            start = self.request_counts[provider]
            try:
                candidates, note = getattr(self, "_" + provider)(normalized)
                smiles, status, validation_note = self._select(candidates)
                note = note or validation_note
            except _ProviderFailure as exc:
                smiles, status, note = "", exc.status, exc.note
            except (KeyError, TypeError, AttributeError) as exc:
                smiles, status, note = "", "error", "Provider returned an unexpected response structure."
            trace.append({"provider": provider, "status": status,
                          "requests": self.request_counts[provider] - start, "note": note})
            result = Resolution(normalized, smiles, provider, status, note, trace)
            if status in ("found", "ambiguous"):
                return result
            if priority[status] >= priority[final.status]:
                final = result
        final.trace = trace
        return final
