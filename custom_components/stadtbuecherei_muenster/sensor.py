"""Sensors for the Stadtbücherei Münster."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import StadtbuechereiConfigEntry
from .entity import AccountEntity, LoanEntity, setup_loan_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StadtbuechereiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            LoanCountSensor(coordinator, "loans"),
            NextDueSensor(coordinator, "next_due"),
            ReservationsSensor(coordinator, "reservations"),
            ReadyForPickupSensor(coordinator, "ready_for_pickup"),
            FeesSensor(coordinator, "fees"),
        ]
    )
    setup_loan_entities(
        coordinator,
        async_add_entities,
        lambda copy_id: [DueDateSensor(coordinator, copy_id, "due_date")],
    )


class LoanCountSensor(AccountEntity, SensorEntity):
    """Number of borrowed media, with the full list as attribute."""

    _attr_native_unit_of_measurement = "Medien"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.loans)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loans = sorted(
            self.coordinator.data.loans.values(), key=lambda l: l.due_date or date.max
        )
        return {
            "loans": [
                {
                    "title": loan.title,
                    "author": loan.author,
                    "media_group": loan.media_group,
                    "branch": loan.branch,
                    "due_date": loan.due_date.isoformat() if loan.due_date else None,
                    "renewable": loan.renewable,
                    "copy_id": loan.copy_id,
                }
                for loan in loans
            ]
        }


class NextDueSensor(AccountEntity, SensorEntity):
    """Earliest due date across all loans."""

    _attr_device_class = SensorDeviceClass.DATE

    @property
    def native_value(self) -> date | None:
        dates = [l.due_date for l in self.coordinator.data.loans.values() if l.due_date]
        return min(dates, default=None)


def _json_ready(item: Any) -> dict[str, Any]:
    """Dataclass as attribute dict with ISO dates."""
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in asdict(item).items()
    }


class ReservationsSensor(AccountEntity, SensorEntity):
    """Reserved media that are not yet ready for pickup."""

    _attr_native_unit_of_measurement = "Medien"

    def _items(self) -> list:
        return [r for r in self.coordinator.data.reservations if not r.ready_for_pickup]

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"reservations": [_json_ready(r) for r in self._items()]}


class ReadyForPickupSensor(ReservationsSensor):
    """Reserved media waiting at the library."""

    def _items(self) -> list:
        return [r for r in self.coordinator.data.reservations if r.ready_for_pickup]


class FeesSensor(AccountEntity, SensorEntity):
    """Sum of open fees."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.fees_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fees": [_json_ready(fee) for fee in self.coordinator.data.fees]}


class DueDateSensor(LoanEntity, SensorEntity):
    """Due date of one medium; the other table columns are attributes."""

    _attr_device_class = SensorDeviceClass.DATE

    @property
    def native_value(self) -> date | None:
        return self.loan.due_date if self.loan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not (loan := self.loan):
            return None
        days_left = (
            (loan.due_date - dt_util.now().date()).days if loan.due_date else None
        )
        return {
            "title": loan.title,
            "author": loan.author,
            "media_group": loan.media_group,
            "branch": loan.branch,
            "days_left": days_left,
            "renewable": loan.renewable,
            "renewal_text": loan.renewal_text,
            "status_message": loan.status_message,
            "catalogue_url": loan.catalogue_url,
            "copy_id": loan.copy_id,
        }
