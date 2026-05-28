"""MPSV Social Services Registry Actor.

Fetches social service listings from the Czech MPSV (Ministry of Labour
and Social Affairs) public registry and pushes them to the default dataset.

Each dataset item represents a single registered social service with its
provider information, service identifiers, and facility details.
"""

from __future__ import annotations

import asyncio
import logging

from apify import Actor

from mpsv_social_services.mpsv_client import (
    DRUH_SLUZBY_LABELS,
    FORMA_SLUZBY_LABELS,
    DruhSluzby,
    FormaSluzby,
    MpsvClient,
    SearchResultItem,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

# Reverse mapping: enum member name -> DruhSluzby value
_DRUH_BY_NAME = {member.name: member for member in DruhSluzby}
_FORMA_BY_NAME = {member.name: member for member in FormaSluzby}


def _serialize_item(item: SearchResultItem, query_label: str) -> dict:
    """Convert a SearchResultItem to a flat-ish dict for the dataset."""
    ico = item.poskytovatel.osoba_poskytovatele and item.poskytovatel.osoba_poskytovatele.ico
    ics = item.sluzba.identifikator

    facilities = []
    for z in item.sluzba.sluzby_v_zarizeni:
        facilities.append({
            'id': z.id_zarizeni,
            'name': z.nazev_zarizeni,
            'serviceFrom': z.poskytovani_sluzby_od,
            'serviceTo': z.poskytovani_sluzby_do,
            'identifier': z.identifikator,
        })

    forms = []
    for f in item.sluzba.formy_socialni_sluzby:
        forms.append({
            'formaId': f.forma_id,
            'continuous': bool(f.nepretrzite_poskytovani),
        })

    return {
        'queryLabel': query_label,
        'providerIco': ico,
        'providerName': item.poskytovatel.nazev_poskytovatele,
        'providerId': item.poskytovatel.id,
        'serviceId': item.sluzba.id,
        'serviceIdentifier': ics,
        'serviceTypeId': item.sluzba.druh_socialni_sluzby_id,
        'serviceFrom': item.sluzba.datum_poskytovani_od,
        'serviceTo': item.sluzba.datum_poskytovani_do,
        'facilities': facilities,
        'forms': forms,
    }


def _parse_queries(
    actor_input: dict,
) -> list[tuple[str, DruhSluzby, FormaSluzby | None]]:
    """Build the list of queries from Actor input.

    Input format:
        serviceTypes: list of DruhSluzby enum member names (e.g. ["TISNOVA_PECE", "KRIZOVA_POMOC"])
        serviceForm: optional FormaSluzby enum member name (e.g. "TERENNI")

    If serviceTypes is empty or not provided, ALL service types are queried.
    """
    raw_types: list[str] = actor_input.get('serviceTypes') or []
    raw_form: str | None = actor_input.get('serviceForm')

    # Resolve service types
    if raw_types:
        service_types: list[DruhSluzby] = []
        for name in raw_types:
            if name not in _DRUH_BY_NAME:
                logger.warning('Unknown service type: %s (skipping)', name)
                continue
            service_types.append(_DRUH_BY_NAME[name])
    else:
        service_types = list(DruhSluzby)

    # Resolve optional form filter
    forma: FormaSluzby | None = None
    if raw_form:
        if raw_form not in _FORMA_BY_NAME:
            logger.warning('Unknown service form: %s (ignoring)', raw_form)
        else:
            forma = _FORMA_BY_NAME[raw_form]

    # Build query tuples
    queries: list[tuple[str, DruhSluzby, FormaSluzby | None]] = []
    for druh in service_types:
        label = DRUH_SLUZBY_LABELS.get(druh, druh.name)
        if forma:
            label = f'{label} – {FORMA_SLUZBY_LABELS.get(forma, forma.name)}'
        queries.append((label, druh, forma))

    return queries


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        queries = _parse_queries(actor_input)

        logger.info('Will run %d queries.', len(queries))

        client = MpsvClient()
        total_count = 0

        for label, druh_id, forma_id in queries:
            logger.info('Fetching: %s ...', label)
            items = await client.search_all(druh_sluzby_id=druh_id, forma_id=forma_id)
            logger.info('  -> %d results', len(items))

            if items:
                await Actor.push_data([_serialize_item(item, label) for item in items])
                total_count += len(items)

        logger.info('Done. Pushed %d service records total.', total_count)


if __name__ == '__main__':
    asyncio.run(main())
