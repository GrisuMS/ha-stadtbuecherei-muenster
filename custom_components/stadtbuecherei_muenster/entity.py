"""Base entities for the Stadtbücherei Münster."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Loan
from .const import DOMAIN, MANUFACTURER
from .coordinator import StadtbuechereiConfigEntry, StadtbuechereiCoordinator


def account_device_info(entry: StadtbuechereiConfigEntry) -> DeviceInfo:
    """Device representing the library account."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Stadtbücherei Münster ({entry.title})",
        manufacturer=MANUFACTURER,
        configuration_url="https://open.stadt-muenster.de/Kundenkonto",
    )


def _account_area_name(coordinator: StadtbuechereiCoordinator) -> str | None:
    """Area of the account device, so newly borrowed media land in the same area.

    Home Assistant applies a suggested area only when it creates a device,
    so media the user has moved elsewhere stay where they are.
    """
    hass = coordinator.hass
    entry_id = coordinator.config_entry.entry_id
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry_id), entry_id
    )
    if device is None or device.area_id is None:
        return None
    area = ar.async_get(hass).async_get_area(device.area_id)
    return area.name if area else None


class AccountEntity(CoordinatorEntity[StadtbuechereiCoordinator]):
    """Entity belonging to the account device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: StadtbuechereiCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = account_device_info(entry)


class LoanEntity(CoordinatorEntity[StadtbuechereiCoordinator]):
    """Entity belonging to one borrowed medium; each medium is its own device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: StadtbuechereiCoordinator, copy_id: str, key: str
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        loan = coordinator.data.loans[copy_id]
        self.copy_id = copy_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{copy_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{copy_id}")},
            name=loan.title,
            manufacturer=loan.author,
            model=loan.media_group,
            configuration_url=loan.catalogue_url,
            via_device=(DOMAIN, entry.entry_id),
            suggested_area=_account_area_name(coordinator),
        )

    @property
    def loan(self) -> Loan | None:
        return self.coordinator.data.loans.get(self.copy_id)

    @property
    def available(self) -> bool:
        return super().available and self.loan is not None


def setup_loan_entities(
    coordinator: StadtbuechereiCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Add entities for every loan, now and whenever new media are borrowed."""
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = set(coordinator.data.loans) - known
        known.update(new)
        known.intersection_update(coordinator.data.loans)
        if new:
            async_add_entities(
                [entity for copy_id in new for entity in factory(copy_id)]
            )

    _add_new()
    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(_add_new))
