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
from pydantic import BaseModel, Field, computed_field, field_validator

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


class ActorInput(BaseModel):
    """Pydantic model for the Actor input.

    Accepts enum member *names* (strings) from JSON and resolves them to the
    actual enum values.  Unknown names are logged and skipped.  An empty /
    missing ``serviceTypes`` means "all types"; an empty / missing
    ``serviceForms`` means "no form filtering".
    """

    service_types: list[DruhSluzby] = Field(default_factory=list, alias='serviceTypes')
    service_forms: set[FormaSluzby] = Field(default_factory=set, alias='serviceForms')

    model_config = {'populate_by_name': True}

    @field_validator('service_types', mode='before')
    @classmethod
    def _resolve_service_types(cls, v: list[str] | None) -> list[DruhSluzby]:
        if not v:
            return list(DruhSluzby)
        resolved: list[DruhSluzby] = []
        for name in v:
            if name not in _DRUH_BY_NAME:
                logger.warning('Unknown service type: %s (skipping)', name)
                continue
            resolved.append(_DRUH_BY_NAME[name])
        return resolved

    @field_validator('service_forms', mode='before')
    @classmethod
    def _resolve_service_forms(cls, v: list[str] | None) -> set[FormaSluzby]:
        if not v:
            return set()
        resolved: set[FormaSluzby] = set()
        for name in v:
            if name not in _FORMA_BY_NAME:
                logger.warning('Unknown service form: %s (skipping)', name)
                continue
            resolved.add(_FORMA_BY_NAME[name])
        return resolved

    @computed_field  # type: ignore[prop-decorator]
    @property
    def service_form_labels(self) -> str:
        """Comma-separated human-readable labels for the selected service forms."""
        return ', '.join(sorted(FORMA_SLUZBY_LABELS.get(f, str(f)) for f in self.service_forms))

    def druh_label(self, druh: DruhSluzby) -> str:
        """Human-readable label for a service type enum value."""
        return DRUH_SLUZBY_LABELS.get(druh, druh.name)


def _resolve_forma_label(forma_id: int) -> str | None:
    """Look up the human-readable label for a forma ID, or None if unknown."""
    try:
        return FORMA_SLUZBY_LABELS[FormaSluzby(forma_id)]
    except (ValueError, KeyError):
        return None


class FacilityItem(BaseModel):
    """A single facility (zarizeni) in the dataset output."""

    id: int = Field(alias='id')
    name: str = Field(alias='name')
    service_from: str | None = Field(None, alias='serviceFrom')
    service_to: str | None = Field(None, alias='serviceTo')
    identifier: str | None = Field(None, alias='identifier')

    model_config = {'populate_by_name': True}


class FormItem(BaseModel):
    """A single form-of-service entry in the dataset output."""

    forma_id: int = Field(alias='formaId')
    forma_label: str | None = Field(None, alias='formaLabel')
    continuous: bool = Field(alias='continuous')

    model_config = {'populate_by_name': True}


class DatasetItem(BaseModel):
    """A single service record pushed to the Apify dataset."""

    service_type: str = Field(alias='serviceType')
    provider_ico: str | None = Field(None, alias='providerIco')
    provider_name: str = Field(alias='providerName')
    provider_id: int = Field(alias='providerId')
    service_id: int = Field(alias='serviceId')
    service_identifier: str | None = Field(None, alias='serviceIdentifier')
    service_type_id: int = Field(alias='serviceTypeId')
    service_from: str | None = Field(None, alias='serviceFrom')
    service_to: str | None = Field(None, alias='serviceTo')
    service_form_labels: list[str] = Field(alias='serviceFormLabels')
    facilities: list[FacilityItem] = Field(alias='facilities')
    forms: list[FormItem] = Field(alias='forms')

    model_config = {'populate_by_name': True}

    @classmethod
    def from_search_result(cls, item: SearchResultItem, query_label: str) -> DatasetItem:
        """Build a DatasetItem from an API search result and a query label."""
        ico = item.poskytovatel.osoba_poskytovatele and item.poskytovatel.osoba_poskytovatele.ico

        facilities = [
            FacilityItem(
                id=z.id_zarizeni,
                name=z.nazev_zarizeni,
                service_from=z.poskytovani_sluzby_od,
                service_to=z.poskytovani_sluzby_do,
                identifier=z.identifikator,
            )
            for z in item.sluzba.sluzby_v_zarizeni
        ]

        forms = [
            FormItem(
                forma_id=f.forma_id,
                forma_label=_resolve_forma_label(f.forma_id),
                continuous=bool(f.nepretrzite_poskytovani),
            )
            for f in item.sluzba.formy_socialni_sluzby
        ]

        form_labels = [f.forma_label for f in forms if f.forma_label]

        return cls(
            service_type=query_label,
            provider_ico=ico,
            provider_name=item.poskytovatel.nazev_poskytovatele,
            provider_id=item.poskytovatel.id,
            service_id=item.sluzba.id,
            service_identifier=item.sluzba.identifikator,
            service_type_id=item.sluzba.druh_socialni_sluzby_id,
            service_from=item.sluzba.datum_poskytovani_od,
            service_to=item.sluzba.datum_poskytovani_do,
            service_form_labels=form_labels,
            facilities=facilities,
            forms=forms,
        )


def _item_matches_forms(item: SearchResultItem, form_filter: set[FormaSluzby]) -> bool:
    """Check whether a service has at least one of the requested forms."""
    if not form_filter:
        return True
    return any(FormaSluzby(f.forma_id) in form_filter for f in item.sluzba.formy_socialni_sluzby)


async def main() -> None:
    async with Actor:
        actor_input = ActorInput.model_validate(await Actor.get_input() or {})

        if actor_input.service_forms:
            logger.info(
                'Will query %d service types, filtering to forms: %s',
                len(actor_input.service_types),
                actor_input.service_form_labels,
            )
        else:
            logger.info('Will query %d service types (all forms).', len(actor_input.service_types))

        client = MpsvClient()
        total_count = 0

        for druh in actor_input.service_types:
            label = actor_input.druh_label(druh)
            logger.info('Fetching: %s ...', label)
            items = await client.search_all(druh_sluzby_id=druh)
            logger.info('  -> %d results', len(items))

            matched = [item for item in items if _item_matches_forms(item, actor_input.service_forms)]
            if actor_input.service_forms and len(matched) != len(items):
                logger.info('  -> %d after form filter', len(matched))

            if matched:
                await Actor.push_data([
                    DatasetItem.from_search_result(item, label).model_dump(by_alias=True)
                    for item in matched
                ])
                total_count += len(matched)

        logger.info('Done. Pushed %d service records total.', total_count)


if __name__ == '__main__':
    asyncio.run(main())
