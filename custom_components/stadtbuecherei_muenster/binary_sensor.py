"""Binary sensors for the Stadtbücherei Münster."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

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
        lambda copy_id: [RenewableSensor(coordinator, copy_id, "renewable")],
    )


class RenewableSensor(LoanEntity, BinarySensorEntity):
    """Whether the medium can be renewed right now."""

    @property
    def is_on(self) -> bool | None:
        return self.loan.renewable if self.loan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not (loan := self.loan):
            return None
        return {"renewal_text": loan.renewal_text, "status_message": loan.status_message}
