"""The matrix model as a value object.

Introduced because ``output_count=`` and ``input_count=`` were threaded through roughly forty
protocol functions, each re-validating the range on its own. That is scattered validation and a
missing domain concept: the two numbers always travel together and several opcodes depend on
them, so they deserve a name.
"""

from __future__ import annotations

import pytest
from ams.model import MODELS, MatrixSpec


class TestModelTable:
    def test_every_supported_model_is_square(self) -> None:
        for spec in MODELS.values():
            assert spec.outputs == spec.inputs

    def test_a_model_is_looked_up_by_the_name_the_config_entry_stores(self) -> None:
        assert MatrixSpec.for_model("AMS8").outputs == 8
        assert MatrixSpec.for_model("AMS24").outputs == 24

    def test_an_unknown_model_is_refused_rather_than_guessed(self) -> None:
        with pytest.raises(ValueError):
            MatrixSpec.for_model("AMS99")


class TestChannelValidation:
    def test_a_valid_channel_converts_to_its_zero_based_wire_byte(self) -> None:
        spec = MatrixSpec.for_model("AMS24")
        assert spec.output_byte(1) == 0
        assert spec.output_byte(24) == 23
        assert spec.input_byte(1) == 0

    @pytest.mark.parametrize("channel", [0, -1, 9])
    def test_a_channel_outside_the_matrix_is_refused(self, channel: int) -> None:
        with pytest.raises(ValueError):
            MatrixSpec.for_model("AMS8").output_byte(channel)

    def test_the_error_names_the_matrix_it_was_measured_against(self) -> None:
        """A bare 'invalid output' is useless when three matrices of two sizes are configured."""
        with pytest.raises(ValueError, match="AMS8"):
            MatrixSpec.for_model("AMS8").output_byte(9)


class TestDerivedIndices:
    def test_the_disconnect_sentinel_is_one_past_the_last_input(self) -> None:
        """There is no disconnect opcode; routing to an out-of-range input is how 'off' works."""
        assert MatrixSpec.for_model("AMS8").disconnect_source == 8
        assert MatrixSpec.for_model("AMS24").disconnect_source == 24

    def test_the_asg_trigger_index_depends_on_the_model(self) -> None:
        """An 8x8 has one output trigger bank, so ASG lands on index 1 -- the same index a 24x24
        uses for its 9-16 bank. Getting this wrong toggles the wrong bank silently, which is why
        it belongs here once rather than as a comparison at each call site."""
        assert MatrixSpec.for_model("AMS8").asg_index == 1
        assert MatrixSpec.for_model("AMS24").asg_index == 3

    def test_trigger_bank_count_follows_the_output_count(self) -> None:
        assert MatrixSpec.for_model("AMS8").trigger_banks == 1
        assert MatrixSpec.for_model("AMS24").trigger_banks == 3

    def test_a_trigger_bank_the_matrix_does_not_have_is_refused(self) -> None:
        with pytest.raises(ValueError):
            MatrixSpec.for_model("AMS8").trigger_bank_byte(2)
        assert MatrixSpec.for_model("AMS24").trigger_bank_byte(2) == 1


class TestConnectorKinds:
    """Which inputs are analog, digital, or a shared connector -- per the installation guide.

    | Model | Inputs                                    | Outputs        |
    |-------|-------------------------------------------|----------------|
    | AMS8  | 1-4 analog, 5-8 analog **or** digital     | 1-8 analog     |
    | AMS24 | 1-16 analog, 17-24 digital                | 1-24 analog    |

    The AMS8's 5-8 are one connector pair each, "either digital or analog, but not both", which
    is why they are a distinct kind rather than being listed twice. Every output on both models
    is analog RCA; there is no digital output on this hardware.
    """

    def test_the_ams8_shares_its_last_four_inputs_between_analog_and_digital(self) -> None:
        spec = MatrixSpec.for_model("AMS8")
        assert [spec.input_kind(i).value for i in (1, 4)] == ["analog", "analog"]
        assert [spec.input_kind(i).value for i in (5, 8)] == ["shared", "shared"]

    def test_the_ams24_puts_its_digital_inputs_last_and_shares_nothing(self) -> None:
        spec = MatrixSpec.for_model("AMS24")
        assert [spec.input_kind(i).value for i in (1, 16)] == ["analog", "analog"]
        assert [spec.input_kind(i).value for i in (17, 24)] == ["digital", "digital"]

    def test_index_five_is_not_the_same_kind_on_both_models(self) -> None:
        """The reason the kind cannot be derived from the index alone, and so cannot be a static
        label: input 5 is a shared connector on an AMS8 and plain analog on an AMS24."""
        assert MatrixSpec.for_model("AMS8").input_kind(5) is not MatrixSpec.for_model(
            "AMS24"
        ).input_kind(5)

    def test_every_output_is_analog_on_every_model(self) -> None:
        for spec in MODELS.values():
            assert all(spec.output_kind(o).value == "analog" for o in (1, spec.outputs))

    def test_a_channel_the_matrix_does_not_have_is_refused(self) -> None:
        with pytest.raises(ValueError):
            MatrixSpec.for_model("AMS8").input_kind(9)
        with pytest.raises(ValueError):
            MatrixSpec.for_model("AMS8").output_kind(0)


class TestOnlyRealModelsAreOffered:
    def test_ams16_is_not_a_product(self) -> None:
        """Removed 2026-08-29. It was never measured and never sourced -- it entered through the
        design doc and propagated into the model table, the README and the protocol reference.
        The installation guide lists TS-AMS8 and TS-AMS24 only, and the two Control4 driver
        archives are AMS8 and AMS24. Offering it invited a channel count nothing could support.
        """
        assert "AMS16" not in MODELS
        with pytest.raises(ValueError):
            MatrixSpec.for_model("AMS16")
