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
        assert MatrixSpec.for_model("AMS8") == MatrixSpec(name="AMS8", outputs=8, inputs=8)
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
        assert MatrixSpec.for_model("AMS16").trigger_banks == 2
        assert MatrixSpec.for_model("AMS24").trigger_banks == 3

    def test_a_trigger_bank_the_matrix_does_not_have_is_refused(self) -> None:
        with pytest.raises(ValueError):
            MatrixSpec.for_model("AMS8").trigger_bank_byte(2)
        assert MatrixSpec.for_model("AMS24").trigger_bank_byte(2) == 1
