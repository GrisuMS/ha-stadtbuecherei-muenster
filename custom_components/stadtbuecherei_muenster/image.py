"""Cover images for the Stadtbücherei Münster."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import StadtbuechereiConfigEntry, StadtbuechereiCoordinator
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
        lambda copy_id: [CoverImage(hass, coordinator, copy_id)],
    )


class CoverImage(LoanEntity, ImageEntity):
    """Cover of the borrowed medium."""

    def __init__(
        self, hass: HomeAssistant, coordinator: StadtbuechereiCoordinator, copy_id: str
    ) -> None:
        LoanEntity.__init__(self, coordinator, copy_id, "cover")
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.covers.get(self.copy_id) is not None

    async def async_image(self) -> bytes | None:
        if (cover := self.coordinator.covers.get(self.copy_id)) is None:
            return None
        self._attr_content_type = cover[1]
        return cover[0]
