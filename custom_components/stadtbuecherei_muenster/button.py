"""Renew buttons for the Stadtbücherei Münster."""

from __future__ import annotations

import aiohttp
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import LibraryError
from .const import DOMAIN
from .coordinator import StadtbuechereiConfigEntry
from .entity import LoanEntity, setup_loan_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StadtbuechereiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    setup_loan_entities(
        coordinator,
        async_add_entities,
        lambda copy_id: [RenewButton(coordinator, copy_id, "renew")],
    )


class RenewButton(LoanEntity, ButtonEntity):
    """Renews the medium in the library account."""

    async def async_press(self) -> None:
        if (loan := self.loan) is None:
            raise HomeAssistantError("Medium ist nicht mehr ausgeliehen")
        if loan.renewable is False:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_renewable",
                translation_placeholders={
                    "title": loan.title,
                    "reason": loan.status_message or "-",
                },
            )
        try:
            await self.coordinator.client.async_renew(self.copy_id)
        except (LibraryError, aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="renew_failed",
                translation_placeholders={"title": loan.title, "error": str(err)},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()
