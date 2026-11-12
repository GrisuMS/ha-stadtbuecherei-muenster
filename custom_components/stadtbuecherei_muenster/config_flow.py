"""Config flow for the Stadtbücherei Münster."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from . import create_session
from .api import AuthenticationError, LibraryError, StadtbuechereiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class StadtbuechereiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for library card number and password."""

    VERSION = 1

    async def _validate(self, username: str, password: str) -> str | None:
        """Return an error key, or None if the login works."""
        client = StadtbuechereiClient(create_session(self.hass), username, password)
        try:
            await client.async_validate_login()
        except AuthenticationError:
            return "invalid_auth"
        except (LibraryError, aiohttp.ClientError, TimeoutError):
            _LOGGER.exception("Cannot reach the library catalogue")
            return "cannot_connect"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()
            if not (error := await self._validate(username, user_input[CONF_PASSWORD])):
                return self.async_create_entry(
                    title=f"Ausweis {username}",
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            if not (
                error := await self._validate(
                    entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            ):
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
