"""Data update coordinator for the Stadtbücherei Münster."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AccountData, AuthenticationError, LibraryError, StadtbuechereiClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type StadtbuechereiConfigEntry = ConfigEntry[StadtbuechereiCoordinator]


class StadtbuechereiCoordinator(DataUpdateCoordinator[AccountData]):
    """Polls the account and caches cover images."""

    config_entry: StadtbuechereiConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: StadtbuechereiConfigEntry,
        client: StadtbuechereiClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.covers: dict[str, tuple[bytes, str] | None] = {}

    async def _async_update_data(self) -> AccountData:
        try:
            data = await self.client.async_get_account()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(err) from err
        except (LibraryError, aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error talking to the library: {err}") from err

        for copy_id, loan in data.loans.items():
            if copy_id not in self.covers:
                self.covers[copy_id] = await self.client.async_get_cover(loan)
        for copy_id in set(self.covers) - set(data.loans):
            del self.covers[copy_id]

        self._remove_returned_devices(set(data.loans))
        return data

    def _remove_returned_devices(self, copy_ids: set[str]) -> None:
        """Drop the devices of media that have been returned."""
        registry = dr.async_get(self.hass)
        prefix = f"{self.config_entry.entry_id}_"
        for device in dr.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            for domain, identifier in device.identifiers:
                if (
                    domain == DOMAIN
                    and identifier.startswith(prefix)
                    and identifier[len(prefix) :] not in copy_ids
                ):
                    registry.async_update_device(
                        device.id, remove_config_entry_id=self.config_entry.entry_id
                    )
