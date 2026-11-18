"""Config flow for Valetudo Camera."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.helpers.typing import ConfigType

from .api import (
    ValetudoAuthError,
    ValetudoCertificateError,
    ValetudoClient,
    ValetudoConnectionError,
    ValetudoNotSupportedError,
    ValetudoRobotInfo,
    ValetudoStreamingDisabledError,
)
from .const import (
    CONF_MJPEG_FPS,
    CONF_MJPEG_QUALITY,
    CONF_PASSWORD,
    CONF_SNAPSHOT_CACHE,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_MJPEG_FPS,
    DEFAULT_MJPEG_QUALITY,
    DEFAULT_SNAPSHOT_CACHE,
    DOMAIN,
)

_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class ValetudoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Valetudo Camera."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered_url: str | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None
        # Form input waiting for the user to accept an untrusted certificate,
        # and the step ("user" or "reconfigure") it came from.
        self._pending_input: dict[str, Any] | None = None
        self._pending_step = "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a robot manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info, error = await self._async_check_input(user_input)
            if error == "invalid_certificate":
                return await self._async_ask_certificate(user_input, "user")
            if info is not None:
                return await self._async_create(info, user_input)
            errors["base"] = error or "cannot_connect"
        return self._show_user_form(errors)

    def _show_user_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(self._discovered_url),
            errors=errors,
        )

    async def _async_create(
        self, info: ValetudoRobotInfo, data: dict[str, Any]
    ) -> ConfigFlowResult:
        # A pending discovery of the same robot must not block manual
        # setup; Home Assistant removes it once the entry exists.
        await self.async_set_unique_id(info.system_id, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{info.manufacturer} {info.model}", data=data
        )

    async def _async_check_input(
        self, data: dict[str, Any]
    ) -> tuple[ValetudoRobotInfo | None, str | None]:
        """Validate form input; return the robot or the error translation key."""
        try:
            data[CONF_URL] = _validate_url(data[CONF_URL])
            return await self._async_validate_robot(data), None
        except ValetudoAuthError:
            return None, "invalid_auth"
        except ValetudoNotSupportedError:
            return None, "not_supported"
        except ValetudoStreamingDisabledError:
            return None, "streaming_disabled"
        except ValetudoCertificateError:
            return None, "invalid_certificate"
        except ValetudoConnectionError, ValueError:
            return None, "cannot_connect"

    async def _async_ask_certificate(
        self, data: dict[str, Any], step: str
    ) -> ConfigFlowResult:
        self._pending_input = data
        self._pending_step = step
        return await self.async_step_certificate()

    async def async_step_certificate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before connecting to a robot whose certificate is not trusted.

        Verification is never switched off silently: the user has to confirm
        this step, which is only shown after a certificate check failed.
        """
        assert self._pending_input is not None
        if user_input is None:
            return self.async_show_form(
                step_id="certificate",
                description_placeholders={"url": self._pending_input[CONF_URL]},
            )
        data = {**self._pending_input, CONF_VERIFY_SSL: False}
        reconfigure = self._pending_step == "reconfigure"
        info, error = await self._async_check_input(data)
        if info is None:
            errors = {"base": error or "cannot_connect"}
            if reconfigure:
                return self._show_reconfigure_form(errors)
            return self._show_user_form(errors)
        if reconfigure:
            return await self._async_finish_reconfigure(info, data)
        return await self._async_create(info, data)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Start manual confirmation for a discovered Valetudo robot."""
        # Valetudo announces its systemId as TXT record "id". Using it as the
        # unique ID stops repeated announcements from opening further dialogs.
        # The stored URL is left untouched: it may be a reverse proxy.
        if system_id := discovery_info.properties.get("id"):
            await self.async_set_unique_id(system_id)
            self._abort_if_unique_id_configured()
        else:
            await self._async_handle_discovery_without_unique_id()
        self._discovered_url = _discovered_url(discovery_info)
        self.context["title_placeholders"] = {"name": _discovery_name(discovery_info)}
        return await self.async_step_user()

    async def async_step_reauth(self, entry_data: ConfigType) -> ConfigFlowResult:
        """Start reauthentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm updated credentials."""
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="reauth_entry_not_found")
        errors: dict[str, str] = {}
        if user_input is not None:
            updated = {**entry.data, **user_input}
            try:
                updated[CONF_URL] = _validate_url(updated[CONF_URL])
                await self._async_validate_robot(updated)
            except ValetudoAuthError:
                errors["base"] = "invalid_auth"
            except ValetudoNotSupportedError:
                errors["base"] = "not_supported"
            except ValetudoConnectionError, ValetudoStreamingDisabledError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(entry.data),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the robot URL or credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            stored = self._get_reconfigure_entry().data
            # The stored password is never sent to the browser. An empty field
            # keeps it; clearing the username removes authentication.
            if user_input.get(CONF_USERNAME) and not user_input.get(CONF_PASSWORD):
                user_input = {
                    **user_input,
                    CONF_PASSWORD: stored.get(CONF_PASSWORD, ""),
                }
            # Always try with verification first, so moving from a self-signed
            # to a trusted URL switches verification back on.
            user_input = {**user_input, CONF_VERIFY_SSL: True}
            info, error = await self._async_check_input(user_input)
            if error == "invalid_certificate":
                return await self._async_ask_certificate(user_input, "reconfigure")
            if info is not None:
                return await self._async_finish_reconfigure(info, user_input)
            errors["base"] = error or "cannot_connect"
        return self._show_reconfigure_form(errors)

    def _show_reconfigure_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(defaults=self._get_reconfigure_entry().data),
            errors=errors,
        )

    async def _async_finish_reconfigure(
        self, info: ValetudoRobotInfo, data: dict[str, Any]
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(info.system_id)
        self._abort_if_unique_id_mismatch()
        return self.async_update_reload_and_abort(
            self._get_reconfigure_entry(), data_updates=data
        )

    async def _async_validate_robot(self, data: dict[str, Any]) -> ValetudoRobotInfo:
        """Validate credentials and ensure duststreaming is enabled."""
        session = async_get_clientsession(
            self.hass, verify_ssl=data.get(CONF_VERIFY_SSL, True)
        )
        client = ValetudoClient(
            session,
            data[CONF_URL].rstrip("/"),
            data.get(CONF_USERNAME),
            data.get(CONF_PASSWORD),
        )
        info = await client.async_get_robot_info()
        state = await client.async_get_duststreaming_state()
        if not state.enabled:
            raise ValetudoStreamingDisabledError
        return info

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowWithReload:
        """Get the options flow."""
        return ValetudoOptionsFlow()


class ValetudoOptionsFlow(OptionsFlowWithReload):
    """Configure camera stream options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SNAPSHOT_CACHE,
                        default=self.config_entry.options.get(
                            CONF_SNAPSHOT_CACHE, DEFAULT_SNAPSHOT_CACHE
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=300, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_MJPEG_FPS,
                        default=self.config_entry.options.get(
                            CONF_MJPEG_FPS, DEFAULT_MJPEG_FPS
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=20, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_MJPEG_QUALITY,
                        default=self.config_entry.options.get(
                            CONF_MJPEG_QUALITY, DEFAULT_MJPEG_QUALITY
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=2, max=31, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
        )


def _validate_url(value: str) -> str:
    """Reject malformed URLs or URLs with embedded credentials."""
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("URL must be an HTTP(S) base URL without embedded credentials")
    return normalized


def _connection_schema(
    discovered_url: str | None = None,
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the connection form schema."""
    values = defaults or {}
    default_url = values.get(CONF_URL, discovered_url or "")
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=default_url): TextSelector(),
            vol.Optional(
                CONF_USERNAME, default=values.get(CONF_USERNAME, "")
            ): TextSelector(),
            vol.Optional(CONF_PASSWORD, default=""): _PASSWORD_SELECTOR,
        }
    )


def _discovery_name(discovery_info: ZeroconfServiceInfo) -> str:
    """Build a readable card title from Valetudo's mDNS TXT records."""
    props = discovery_info.properties
    model = " ".join(
        part for part in (props.get("manufacturer"), props.get("model")) if part
    )
    name = props.get("name") or props.get("id") or discovery_info.host
    return f"{name} ({model})" if model else name


def _discovered_url(discovery_info: ZeroconfServiceInfo) -> str:
    """Build the base URL, leaving out the default port."""
    host = discovery_info.host
    if ":" in host:
        host = f"[{host}]"  # IPv6 literal
    port = discovery_info.port
    return f"http://{host}" if port in (None, 80) else f"http://{host}:{port}"


def _credentials_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the reauthentication form schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): TextSelector(),
            vol.Optional(CONF_PASSWORD, default=""): _PASSWORD_SELECTOR,
        }
    )
