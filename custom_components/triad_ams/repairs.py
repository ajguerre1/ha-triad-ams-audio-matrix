"""Repair issues.

One issue, deliberately. A repairs list full of things the user cannot act on trains people to
ignore it, so the bar here is: the user can fix it, and without this they would not know why
something is broken.

The one that qualifies is audio sense. When it is disabled on the matrix, every audio-sense entity
reports unavailable at once and the reason is invisible -- the setting lives in the Control4
driver, not in Home Assistant, and no amount of looking at Home Assistant will reveal it. That is
precisely the situation a repair issue is for.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

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
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_AUDIO_SENSE_DISABLED,
            translation_placeholders={"matrix": title},
            learn_more_url="https://github.com/ajguerre1/ha-triad-ams-audio-matrix#audio-sense",
        )
        return

    ir.async_delete_issue(hass, DOMAIN, issue_id)
