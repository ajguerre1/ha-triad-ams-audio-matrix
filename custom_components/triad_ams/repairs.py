"""Repair issues.

One issue, deliberately. A repairs list full of things the user cannot act on trains people to
ignore it, so the bar here is: the user can fix it, and without this they would not know why
something is broken.

The one that qualifies is audio sense. When the matrix is not measuring, every audio-sense entity
reports unavailable at once and the reason is invisible.

**This became fixable in place on 2026-08-29 (FR-14).** Previously the remedy lived in the Control4
driver and the issue could only describe it. Now the integration can set it, and doing so from here
rather than pointing at the switch is not a convenience: the switch is disabled by default like
every non-``media_player`` entity, so "turn on the switch" would first mean "find and enable the
entity", which is a worse instruction than a button that does it.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .ams.errors import TriadError
from .const import DOMAIN

ISSUE_AUDIO_SENSE_DISABLED = "audio_sense_disabled"


def async_check_audio_sense(
    hass: HomeAssistant,
    entry_id: str,
    title: str,
    *,
    enabled: bool | None,
    consumers: bool,
) -> None:
    """Raise or clear the audio-sense issue for one matrix.

    Only raised when something is actually listening. A matrix with audio sense off and no
    audio-sense entities enabled is not broken -- that is the shipping default, and flagging it
    would be nagging about a setting nobody is using.
    """
    issue_id = f"{ISSUE_AUDIO_SENSE_DISABLED}_{entry_id}"

    if consumers and enabled is False:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_AUDIO_SENSE_DISABLED,
            translation_placeholders={"matrix": title},
            data={"entry_id": entry_id},
            learn_more_url="https://github.com/ajguerre1/ha-triad-ams-audio-matrix#audio-sense",
        )
        return

    ir.async_delete_issue(hass, DOMAIN, issue_id)


class EnableAudioSenseFlow(RepairsFlow):
    """Turn audio-sense measuring on for the matrix that raised the issue."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict | None = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or not hasattr(entry, "runtime_data"):
            # The matrix was removed or is not loaded. Aborting beats reporting a success that
            # did not happen; the issue clears on its own once the entry is gone.
            return self.async_abort(reason="entry_unavailable")

        coordinator = entry.runtime_data
        try:
            await coordinator.client.set_audio_sense_enabled(enabled=True)
        except TriadError:
            return self.async_abort(reason="cannot_enable")

        # The write is answered by a burst the client drains, so nothing about the reply confirms
        # the setting took. Re-read: the device is the only authority on that.
        await coordinator.async_refresh_sense_settings()
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str] | None,
) -> RepairsFlow:
    """Home Assistant's entry point for fixing an issue this integration raised."""
    return EnableAudioSenseFlow((data or {}).get("entry_id", ""))
