"""Every setup-form field a model can generate must have a label.

The connector kinds only reach the user through ``strings.json``, and nothing else checks that
the two agree. Adding a model, or changing which inputs are shared, silently produces keys with
no translation -- and Home Assistant renders an untranslated key as the raw key, which is the
defect this whole change set exists to fix. It would look exactly like the `input_1` and
`max_volume_1` labels that prompted it.

This runs without importing Home Assistant, which is why ``MatrixSpec`` owns the field names
rather than ``config_flow``: the Windows development box cannot import Home Assistant at all, so
a guard living beside the flow would only ever run in CI.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from ams.model import MODELS, ChannelKind, MatrixSpec

_COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "triad_ams"
_SOURCES = ("strings.json", "translations/en.json")

#: How each kind is written for a user. The suffix in the key is machine-readable; this is not.
EXPECTED_KIND_TEXT = {
    ChannelKind.ANALOG: "Analog",
    ChannelKind.DIGITAL: "Digital",
    ChannelKind.SHARED: "Analog/Digital Shared",
}


def _labels(source: str, section: str, step: str) -> dict[str, str]:
    data = json.loads((_COMPONENT / source).read_text(encoding="utf-8"))
    return data[section]["step"][step]["data"]


def _every_field() -> list[tuple[MatrixSpec, str, str]]:
    """Every (spec, key, expected label) the flows can produce, across all models."""
    out = []
    for spec in MODELS.values():
        for o in range(1, spec.outputs + 1):
            kind = spec.output_kind(o)
            out.append((spec, spec.output_field(o), f"Output {o} ({EXPECTED_KIND_TEXT[kind]})"))
            out.append((spec, f"max_volume_{o}", f"Output {o} maximum volume"))
        for i in range(1, spec.inputs + 1):
            kind = spec.input_kind(i)
            out.append((spec, spec.input_field(i), f"Input {i} ({EXPECTED_KIND_TEXT[kind]})"))
    return out


@pytest.mark.parametrize("source", _SOURCES)
class TestEveryFieldIsLabelled:
    def test_the_options_step_labels_every_field_correctly(self, source: str) -> None:
        labels = _labels(source, "options", "init")
        wrong = [
            (key, want, labels.get(key))
            for _, key, want in _every_field()
            if labels.get(key) != want
        ]
        assert not wrong, f"{len(wrong)} field(s) mislabelled or missing: {wrong[:5]}"

    def test_the_setup_step_labels_every_channel(self, source: str) -> None:
        """The setup step has the checkboxes but no volume caps -- those are options-only."""
        labels = _labels(source, "config", "channels")
        wrong = [
            (key, want, labels.get(key))
            for _, key, want in _every_field()
            if not key.startswith("max_volume_") and labels.get(key) != want
        ]
        assert not wrong, f"{len(wrong)} field(s) mislabelled or missing: {wrong[:5]}"


class TestTheLabelsSayWhatTheManualSays:
    """Spot checks against the installation guide, so a wholesale regeneration cannot drift.

    The guide: the AMS8 has 8 analog inputs of which 5-8 are "either digital or analog, but not
    both"; the AMS24 has analog 1-16 and digital 17-24. Every output on both is analog RCA.
    """

    def test_an_ams8_shared_input_reads_as_shared(self) -> None:
        labels = _labels("strings.json", "options", "init")
        spec = MatrixSpec.for_model("AMS8")
        assert labels[spec.input_field(5)] == "Input 5 (Analog/Digital Shared)"
        assert labels[spec.input_field(4)] == "Input 4 (Analog)"

    def test_an_ams24_digital_input_reads_as_digital(self) -> None:
        labels = _labels("strings.json", "options", "init")
        spec = MatrixSpec.for_model("AMS24")
        assert labels[spec.input_field(17)] == "Input 17 (Digital)"
        assert labels[spec.input_field(16)] == "Input 16 (Analog)"

    def test_the_same_index_reads_differently_on_the_two_models(self) -> None:
        """The whole reason the kind is in the key rather than the label being per-index."""
        labels = _labels("strings.json", "options", "init")
        ams8, ams24 = MatrixSpec.for_model("AMS8"), MatrixSpec.for_model("AMS24")
        assert labels[ams8.input_field(5)] != labels[ams24.input_field(5)]

    def test_outputs_are_analog_and_say_so(self) -> None:
        labels = _labels("strings.json", "options", "init")
        spec = MatrixSpec.for_model("AMS24")
        assert labels[spec.output_field(24)] == "Output 24 (Analog)"
