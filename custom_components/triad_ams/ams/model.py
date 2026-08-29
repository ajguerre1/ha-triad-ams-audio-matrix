"""The matrix model as a value object.

The device reports neither its model nor its channel count, so the count comes from configuration
and then has to reach every command that carries a channel index. Passing two loose integers
around meant forty-odd functions each re-validating a range, and a bare ``output_count <= 8``
comparison standing in for a model-dependent opcode.

``MatrixSpec`` gives the pair a name, validates once, and puts the derived indices where they can
only be defined a single way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Outputs per 12 V trigger bank, per the protocol's bank layout (1-8, 9-16, 17-24).
_OUTPUTS_PER_TRIGGER_BANK: Final = 8


@dataclass(frozen=True, slots=True)
class MatrixSpec:
    """One matrix model: how many channels it has, and what follows from that."""

    name: str
    outputs: int
    inputs: int

    @classmethod
    def for_model(cls, name: str) -> MatrixSpec:
        """Look up a model by the name a config entry stores.

        Raises rather than defaulting. A wrong channel count is not a cosmetic error: it decides
        the disconnect sentinel and the ASG trigger index, so guessing here produces commands
        that are accepted by the device and do the wrong thing.
        """
        try:
            return MODELS[name]
        except KeyError:
            known = ", ".join(MODELS)
            msg = f"unknown Triad model {name!r}; expected one of {known}"
            raise ValueError(msg) from None

    # -- channel validation -------------------------------------------------------------------

    def output_byte(self, output: int) -> int:
        """Validate a 1-based output and return its 0-based wire byte."""
        return self._byte(output, self.outputs, "output")

    def input_byte(self, source: int) -> int:
        """Validate a 1-based input and return its 0-based wire byte."""
        return self._byte(source, self.inputs, "input")

    def _byte(self, channel: int, count: int, what: str) -> int:
        if not 1 <= channel <= count:
            # Name the model: three matrices of two different sizes can be configured at once,
            # and "output 9 is invalid" does not say which of them was being addressed.
            msg = f"{what} {channel} outside 1..{count} on a {self.name}"
            raise ValueError(msg)
        return channel - 1

    # -- derived indices ----------------------------------------------------------------------

    @property
    def disconnect_source(self) -> int:
        """The input index that means 'no source'.

        There is no disconnect opcode. The device treats an index one past the last valid input
        as silence, so this is ``inputs`` and not ``inputs - 1``.
        """
        return self.inputs

    @property
    def trigger_banks(self) -> int:
        """How many 12 V output trigger banks this model has."""
        return -(-self.outputs // _OUTPUTS_PER_TRIGGER_BANK)

    @property
    def asg_index(self) -> int:
        """Wire index of the ASG trigger, which sits after the last output bank.

        Model-dependent, and quietly so: an 8x8 has one output bank, putting ASG at index 1 --
        exactly the index a 24x24 uses for its 9-16 bank. Addressing it without the model toggles
        the wrong bank on a 24x24 and reports success.
        """
        return self.trigger_banks

    def trigger_bank_byte(self, bank: int) -> int:
        """Validate a 1-based output trigger bank and return its wire byte."""
        if not 1 <= bank <= self.trigger_banks:
            msg = f"trigger bank {bank} outside 1..{self.trigger_banks} on a {self.name}"
            raise ValueError(msg)
        return bank - 1


#: Every model this integration supports. All are square.
MODELS: Final[dict[str, MatrixSpec]] = {
    "AMS8": MatrixSpec(name="AMS8", outputs=8, inputs=8),
    "AMS16": MatrixSpec(name="AMS16", outputs=16, inputs=16),
    "AMS24": MatrixSpec(name="AMS24", outputs=24, inputs=24),
}
