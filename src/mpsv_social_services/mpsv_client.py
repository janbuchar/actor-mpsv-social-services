"""REST API client for the MPSV social services registry (registr-poskytovatelu-sluzeb)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from datetime import date
from enum import IntEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from impit import AsyncClient, TransportError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

BASE_URL = 'https://mpsv.gov.cz/api/api-gateway/rest'

_COMMON_HEADERS = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'Accept-Language': 'cs',
    'Referer': 'https://mpsv.gov.cz/registr-poskytovatelu-sluzeb',
}

# Default cache TTL: 24 hours
_DEFAULT_CACHE_TTL = 86400

# Retry settings for transient HTTP errors (5xx, timeouts, network issues)
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 2.0  # seconds
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DruhSluzby(IntEnum):
    """MPSV service type IDs (druhSocialniSluzbyId).

    Complete codelist from the MPSV ciselniky endpoint:
    /api/ciselniky/rest/ciselniky/DruhSocialniSluzby/polozky
    """

    ODBORNE_SOCIALNI_PORADENSTVI = 3836
    OSOBNI_ASISTENCE = 3837
    PECOVATELSKA_SLUZBA = 3838
    TISNOVA_PECE = 3839
    PRUVODCOVSKE_A_PREDCITATELSKE_SLUZBY = 3840
    PODPORA_SAMOSTATNEHO_BYDLENI = 3841
    ODLEHCOVACI_SLUZBY = 3842
    CENTRA_DENNICH_SLUZEB = 3843
    DENNI_STACIONARE = 3844
    TYDENNI_STACIONARE = 3845
    DOMOVY_PRO_OSOBY_SE_ZDRAVOTNIM_POSTIZENIM = 3846
    DOMOVY_PRO_SENIORY = 3847
    DOMOVY_SE_ZVLASTNIM_REZIMEM = 3848
    CHRANENE_BYDLENI = 3849
    SOCIALNI_SLUZBY_VE_ZDRAVOTNICKYCH_ZARIZENICH = 3850
    RANA_PECE = 3851
    TELEFONICKA_KRIZOVA_POMOC = 3852
    TLUMOCNICKE_SLUZBY = 3853
    AZYLOVE_DOMY = 3854
    DOMY_NA_PUL_CESTY = 3855
    KONTAKTNI_CENTRA = 3856
    KRIZOVA_POMOC = 3857
    NIZKOPRAHOVA_DENNI_CENTRA = 3858
    NIZKOPRAHOVA_ZARIZENI_PRO_DETI_A_MLADEZ = 3859
    NOCLEHARNY = 3860
    SLUZBY_NASLEDNE_PECE = 3861
    SOCIALNE_AKTIVIZACNI_SLUZBY_PRO_RODINY_S_DETMI = 3862
    SOCIALNE_AKTIVIZACNI_SLUZBY_PRO_SENIORY_A_OZP = 3863
    SOCIALNE_TERAPEUTICKE_DILNY = 3864
    TERAPEUTICKE_KOMUNITY = 3865
    TERENNI_PROGRAMY = 3866
    SOCIALNI_REHABILITACE = 3867
    INTERVENCNI_CENTRA = 3868
    CENTRUM_DUSEVNIHO_ZDRAVI = 11078


DRUH_SLUZBY_LABELS: dict[DruhSluzby, str] = {
    DruhSluzby.ODBORNE_SOCIALNI_PORADENSTVI: 'Odborné sociální poradenství',
    DruhSluzby.OSOBNI_ASISTENCE: 'Osobní asistence',
    DruhSluzby.PECOVATELSKA_SLUZBA: 'Pečovatelská služba',
    DruhSluzby.TISNOVA_PECE: 'Tísňová péče',
    DruhSluzby.PRUVODCOVSKE_A_PREDCITATELSKE_SLUZBY: 'Průvodcovské a předčitatelské služby',
    DruhSluzby.PODPORA_SAMOSTATNEHO_BYDLENI: 'Podpora samostatného bydlení',
    DruhSluzby.ODLEHCOVACI_SLUZBY: 'Odlehčovací služby',
    DruhSluzby.CENTRA_DENNICH_SLUZEB: 'Centra denních služeb',
    DruhSluzby.DENNI_STACIONARE: 'Denní stacionáře',
    DruhSluzby.TYDENNI_STACIONARE: 'Týdenní stacionáře',
    DruhSluzby.DOMOVY_PRO_OSOBY_SE_ZDRAVOTNIM_POSTIZENIM: 'Domovy pro osoby se zdravotním postižením',
    DruhSluzby.DOMOVY_PRO_SENIORY: 'Domovy pro seniory',
    DruhSluzby.DOMOVY_SE_ZVLASTNIM_REZIMEM: 'Domovy se zvláštním režimem',
    DruhSluzby.CHRANENE_BYDLENI: 'Chráněné bydlení',
    DruhSluzby.SOCIALNI_SLUZBY_VE_ZDRAVOTNICKYCH_ZARIZENICH: 'Sociální služby poskytované ve zdravotnických zařízeních lůžkové péče',
    DruhSluzby.RANA_PECE: 'Raná péče',
    DruhSluzby.TELEFONICKA_KRIZOVA_POMOC: 'Telefonická krizová pomoc',
    DruhSluzby.TLUMOCNICKE_SLUZBY: 'Tlumočnické služby',
    DruhSluzby.AZYLOVE_DOMY: 'Azylové domy',
    DruhSluzby.DOMY_NA_PUL_CESTY: 'Domy na půl cesty',
    DruhSluzby.KONTAKTNI_CENTRA: 'Kontaktní centra',
    DruhSluzby.KRIZOVA_POMOC: 'Krizová pomoc',
    DruhSluzby.NIZKOPRAHOVA_DENNI_CENTRA: 'Nízkoprahová denní centra',
    DruhSluzby.NIZKOPRAHOVA_ZARIZENI_PRO_DETI_A_MLADEZ: 'Nízkoprahová zařízení pro děti a mládež',
    DruhSluzby.NOCLEHARNY: 'Noclehárny',
    DruhSluzby.SLUZBY_NASLEDNE_PECE: 'Služby následné péče',
    DruhSluzby.SOCIALNE_AKTIVIZACNI_SLUZBY_PRO_RODINY_S_DETMI: 'Sociálně aktivizační služby pro rodiny s dětmi',
    DruhSluzby.SOCIALNE_AKTIVIZACNI_SLUZBY_PRO_SENIORY_A_OZP: 'Sociálně aktivizační služby pro seniory a osoby se zdravotním postižením',
    DruhSluzby.SOCIALNE_TERAPEUTICKE_DILNY: 'Sociálně terapeutické dílny',
    DruhSluzby.TERAPEUTICKE_KOMUNITY: 'Terapeutické komunity',
    DruhSluzby.TERENNI_PROGRAMY: 'Terénní programy',
    DruhSluzby.SOCIALNI_REHABILITACE: 'Sociální rehabilitace',
    DruhSluzby.INTERVENCNI_CENTRA: 'Intervenční centra',
    DruhSluzby.CENTRUM_DUSEVNIHO_ZDRAVI: 'Centrum duševního zdraví',
}


class FormaSluzby(IntEnum):
    """Form of service delivery (formaId)."""

    AMBULANTNI = 3882
    POBYTOVA = 3883
    TERENNI = 3884


FORMA_SLUZBY_LABELS: dict[FormaSluzby, str] = {
    FormaSluzby.AMBULANTNI: 'Ambulantní',
    FormaSluzby.POBYTOVA: 'Pobytová',
    FormaSluzby.TERENNI: 'Terénní',
}


# ---------------------------------------------------------------------------
# Pydantic models – search results
# ---------------------------------------------------------------------------


class Osoba(BaseModel, extra='ignore'):
    ico: str | None = None
    jmeno: str | None = None
    prijmeni: str | None = None
    titul_pred: str | None = None
    titul_za: str | None = None
    nazev_po: str | None = None

    model_config = {'alias_generator': None, 'populate_by_name': True}


class Zarizeni(BaseModel, extra='ignore'):
    id_zarizeni: int
    nazev_zarizeni: str
    poskytovani_sluzby_od: str | None = None
    poskytovani_sluzby_do: str | None = None
    utajena_adresa: bool = False
    kontaktni_adresa: bool = False
    druh_socialni_sluzby_id: int | None = None
    identifikator: str | None = None

    model_config = {
        'alias_generator': None,
        'populate_by_name': True,
        'from_attributes': True,
    }


class FormaDetail(BaseModel, extra='ignore'):
    id: int
    forma_id: int
    nepretrzite_poskytovani: int = 0

    model_config = {'alias_generator': None, 'populate_by_name': True}


class Sluzba(BaseModel, extra='ignore'):
    id: int
    identifikator: str | None = None
    datum_poskytovani_od: str | None = None
    datum_poskytovani_do: str | None = None
    druh_socialni_sluzby_id: int
    sluzby_v_zarizeni: list[Zarizeni] = []
    formy_socialni_sluzby: list[FormaDetail] = []

    model_config = {'alias_generator': None, 'populate_by_name': True}


class OsobaPoskytovatele(BaseModel, extra='ignore'):
    ico: str | None = None
    nazev_po: str | None = None
    jmeno: str | None = None
    prijmeni: str | None = None

    model_config = {'alias_generator': None, 'populate_by_name': True}


class Poskytovatel(BaseModel, extra='ignore'):
    id: int
    nazev_poskytovatele: str
    osoba_poskytovatele: OsobaPoskytovatele | None = None

    model_config = {'alias_generator': None, 'populate_by_name': True}


class SearchResultItem(BaseModel, extra='ignore'):
    sluzba: Sluzba
    poskytovatel: Poskytovatel

    model_config = {'alias_generator': None, 'populate_by_name': True}


class SearchResult(BaseModel):
    total: int
    items: list[SearchResultItem]


# ---------------------------------------------------------------------------
# Pydantic models – spojení (contacts / addresses)
# ---------------------------------------------------------------------------


class TypSpojeni(BaseModel, extra='ignore'):
    id: int
    kod: str
    nazev: str

    model_config = {'alias_generator': None, 'populate_by_name': True}


class StrukturovanaAdresa(BaseModel, extra='ignore'):
    cislo_domovni: int | None = None
    cislo_orientacni: str | None = None
    nazev_ulice: str | None = None
    nazev_obce: str | None = None
    nazev_casti_obce: str | None = None
    nazev_kraje: str | None = None
    nazev_okresu: str | None = None
    psc: str | None = None

    model_config = {'alias_generator': None, 'populate_by_name': True}


class Adresa(BaseModel, extra='ignore'):
    id: int
    strukturovana_adresa: StrukturovanaAdresa | None = None

    model_config = {'alias_generator': None, 'populate_by_name': True}


class Spojeni(BaseModel, extra='ignore'):
    id: int
    subjekt_id: int
    typ_subjektu: str
    email: str | None = None
    telefon: str | None = None
    web: str | None = None
    fax: str | None = None
    datova_schranka: str | None = None
    adresa: Adresa | None = None
    typ_spojeni: TypSpojeni | None = None

    model_config = {'alias_generator': None, 'populate_by_name': True}


# ---------------------------------------------------------------------------
# HTTP cache (SQLite-backed, survives restarts)
# ---------------------------------------------------------------------------


class HttpCache:
    """Simple SQLite-backed HTTP response cache.

    Caches raw response bodies keyed by (method, url, request_body_hash).
    """

    def __init__(self, db_path: str | Path = '.http_cache.db', ttl: int = _DEFAULT_CACHE_TTL) -> None:
        self._ttl = ttl
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                response_body TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def _make_key(self, method: str, url: str, body: bytes | None) -> str:
        h = hashlib.sha256()
        h.update(method.encode())
        h.update(url.encode())
        if body:
            h.update(body)
        return h.hexdigest()

    def get(self, method: str, url: str, body: bytes | None = None) -> tuple[int, str] | None:
        """Return (status_code, response_body) if cached and fresh, else None."""
        key = self._make_key(method, url, body)
        row = self._conn.execute(
            'SELECT status_code, response_body, created_at FROM cache WHERE key = ?', (key,)
        ).fetchone()
        if row is None:
            return None
        status_code, response_body, created_at = row
        if time.time() - created_at > self._ttl:
            self._conn.execute('DELETE FROM cache WHERE key = ?', (key,))
            self._conn.commit()
            return None
        return (status_code, response_body)

    def put(self, method: str, url: str, body: bytes | None, status_code: int, response_body: str) -> None:
        key = self._make_key(method, url, body)
        self._conn.execute(
            'INSERT OR REPLACE INTO cache (key, response_body, status_code, created_at) VALUES (?, ?, ?, ?)',
            (key, response_body, status_code, time.time()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _camelize(data: Any) -> Any:
    """Recursively convert snake_case dict keys to camelCase for Pydantic parsing."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            # Convert camelCase to snake_case
            snake = ''
            for i, ch in enumerate(key):
                if ch.isupper() and i > 0:
                    snake += '_'
                snake += ch.lower()
            result[snake] = _camelize(value)
        return result
    if isinstance(data, list):
        return [_camelize(item) for item in data]
    return data


class MpsvClient:
    """Client for the MPSV registr-poskytovatelu REST API.

    Uses impit for HTTP requests (TLS fingerprint-friendly).
    Caches responses in a local SQLite database to avoid redundant API calls.
    """

    def __init__(self, *, cache_path: str | Path = '.http_cache.db', cache_ttl: int = _DEFAULT_CACHE_TTL) -> None:
        self._client = AsyncClient(browser='firefox', timeout=60)
        self._cache = HttpCache(db_path=cache_path, ttl=cache_ttl)

    # ------------------------------------------------------------------
    # Low-level HTTP with caching
    # ------------------------------------------------------------------

    async def _request(self, method: str, url: str, body: bytes | None = None) -> dict[str, Any]:
        """Make an HTTP request, using cache if available.

        Retries on transient errors (5xx, timeouts, network issues) with exponential backoff.
        """
        cached = self._cache.get(method, url, body)
        if cached is not None:
            status_code, response_body = cached
            logger.debug('Cache HIT: %s %s', method, url)
            return json.loads(response_body)

        logger.debug('Cache MISS: %s %s', method, url)

        last_exception: Exception | None = None
        backoff = _INITIAL_BACKOFF

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                if method == 'GET':
                    response = await self._client.get(url, headers=_COMMON_HEADERS)
                elif method == 'POST':
                    response = await self._client.post(url, headers=_COMMON_HEADERS, content=body)
                else:
                    raise ValueError(f'Unsupported method: {method}')

                status: int = response.status_code  # type: ignore[reportUnknownMemberType]

                if status in _RETRYABLE_STATUS_CODES:
                    logger.warning(
                        'HTTP %d from %s %s (attempt %d/%d), retrying in %.1fs ...',
                        status, method, url, attempt, _MAX_RETRIES, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= _BACKOFF_FACTOR
                    continue

                response.raise_for_status()  # type: ignore[reportUnknownMemberType] - impit has no stubs
                response_text: str = response.text  # type: ignore[reportUnknownMemberType]

                self._cache.put(method, url, body, status, response_text)
                return json.loads(response_text)

            except TransportError as exc:
                last_exception = exc
                logger.warning(
                    '%s on %s %s (attempt %d/%d), retrying in %.1fs ...',
                    type(exc).__name__, method, url, attempt, _MAX_RETRIES, backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= _BACKOFF_FACTOR

        # All retries exhausted — raise the last error we saw
        if last_exception is not None:
            raise last_exception

        # If we got here, all attempts returned retryable status codes
        raise RuntimeError(
            f'Request to {method} {url} failed after {_MAX_RETRIES} retries with HTTP {status}'
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        *,
        druh_sluzby_id: int | DruhSluzby,
        kraj_id: int | None = None,
        forma_id: int | FormaSluzby | None = None,
        reference_date: date | None = None,
        start: int = 0,
        count: int = 10,
    ) -> SearchResult:
        """Search for social services in the registry.

        Args:
            druh_sluzby_id: The ID of the type of social service.
            kraj_id: Region ID to filter by. ``None`` means all regions.
            forma_id: Form of service delivery filter.
            reference_date: The reference date for active-service filtering. Defaults to today.
            start: Pagination offset.
            count: Page size.
        """
        if reference_date is None:
            reference_date = date.today()

        body_dict = self._build_search_body(
            druh_sluzby_id=druh_sluzby_id,
            kraj_id=kraj_id,
            forma_id=forma_id,
            reference_date=reference_date.isoformat(),
            start=start,
            count=count,
        )

        url = f'{BASE_URL}/registr-poskytovatelu/hledani?{urlencode({"v": "rpssv2"})}'
        body = json.dumps(body_dict).encode()
        data = await self._request('POST', url, body)

        raw_items = data.get('list', [])
        items = [SearchResultItem.model_validate(_camelize(item)) for item in raw_items]

        return SearchResult(total=data.get('count', 0), items=items)

    async def search_all(
        self,
        *,
        druh_sluzby_id: int | DruhSluzby,
        kraj_id: int | None = None,
        forma_id: int | FormaSluzby | None = None,
        reference_date: date | None = None,
        page_size: int = 50,
    ) -> list[SearchResultItem]:
        """Paginate through all search results automatically."""
        all_items: list[SearchResultItem] = []
        start = 0

        while True:
            result = await self.search(
                druh_sluzby_id=druh_sluzby_id,
                kraj_id=kraj_id,
                forma_id=forma_id,
                reference_date=reference_date,
                start=start,
                count=page_size,
            )
            all_items.extend(result.items)

            if len(all_items) >= result.total or len(result.items) == 0:
                break

            start += page_size

        return all_items

    # ------------------------------------------------------------------
    # Spojeni (contact info)
    # ------------------------------------------------------------------

    async def get_spojeni(
        self,
        *,
        subjekt_id: int,
        typ_subjektu: str = 'SocialniSluzba',
        kod_typu_spojeni: str | None = None,
    ) -> list[Spojeni]:
        """Fetch contact information (email, phone, web, address) for a subject."""
        params: dict[str, str | int] = {
            'subjektId': subjekt_id,
            'typSubjektu': typ_subjektu,
            'v': '1c5ef6f10dc808d49b3c1ddfa27e6ee9rpssv2',
        }
        if kod_typu_spojeni is not None:
            params['kodTypuSpojeni'] = kod_typu_spojeni

        url = f'{BASE_URL}/adresy/spojeni?{urlencode(params)}'
        data = await self._request('GET', url)

        raw_items = data.get('list', [])
        return [Spojeni.model_validate(_camelize(item)) for item in raw_items]

    async def get_service_contacts(self, service_id: int) -> list[Spojeni]:
        """Shortcut: get all contacts for a social service."""
        return await self.get_spojeni(subjekt_id=service_id, typ_subjektu='SocialniSluzba')

    async def get_facility_address(self, facility_id: int) -> list[Spojeni]:
        """Shortcut: get the address of a facility (zařízení)."""
        return await self.get_spojeni(
            subjekt_id=facility_id,
            typ_subjektu='ZarizeniSocialniSluzby',
            kod_typu_spojeni='adrZar',
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_search_body(
        *,
        druh_sluzby_id: int | DruhSluzby,
        kraj_id: int | None,
        forma_id: int | FormaSluzby | None,
        reference_date: str,
        start: int,
        count: int,
    ) -> dict[str, Any]:
        """Build the Elasticsearch-like query body the MPSV API expects."""
        date_filter: dict[str, Any] = {
            'should': [
                {
                    'must': [
                        {'match': {'field': 'poskytovaniSluzbyOd', 'query': None}},
                        {'match': {'field': 'poskytovaniSluzbyDo', 'query': None}},
                    ]
                },
                {
                    'must': [
                        {'match': {'field': 'poskytovaniSluzbyOd', 'query': None}},
                        {'range': {'field': 'poskytovaniSluzbyDo', 'gte': reference_date}},
                    ]
                },
                {
                    'must': [
                        {'range': {'field': 'poskytovaniSluzbyOd', 'lte': reference_date}},
                        {'match': {'field': 'poskytovaniSluzbyDo', 'query': None}},
                    ]
                },
                {
                    'must': [
                        {'range': {'field': 'poskytovaniSluzbyOd', 'lte': reference_date}},
                        {'range': {'field': 'poskytovaniSluzbyDo', 'gte': reference_date}},
                    ]
                },
            ]
        }

        nested_must: list[dict[str, Any]] = [
            date_filter,
            {'match': {'field': 'utajenaAdresa', 'query': False}},
        ]

        if kraj_id is not None:
            nested_must.append({'match': {'field': 'krajId', 'query': kraj_id}})

        if forma_id is not None:
            nested_must.append({'matchAny': {'field': 'formaId', 'query': [forma_id]}})

        must_clauses: list[dict[str, Any]] = [
            {'matchAny': {'field': 'druhSocialniSluzbyId', 'query': [druh_sluzby_id]}},
            {
                'nested': {
                    'path': '_filtr',
                    'filter': {
                        'must': nested_must,
                    },
                }
            },
        ]

        return {
            'index': ['registr-poskytovatelu'],
            'pagination': {
                'start': start,
                'count': count,
                'order': ['-id'],
            },
            'query': {
                'must': must_clauses,
            },
        }
