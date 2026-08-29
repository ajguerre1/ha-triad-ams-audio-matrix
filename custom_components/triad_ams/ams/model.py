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
from enum import StrEnum
from typing import Final

#: Outputs per 12 V trigger bank, per the protocol's bank layout (1-8, 9-16, 17-24).
_OUTPUTS_PER_TRIGGER_BANK: Final = 8


class ChannelKind(StrEnum):
    """What physically terminates a channel, per the installation guide.

    Not a protocol concept -- the wire treats every input the same. It exists so the setup UI can
    say which socket an index corresponds to, because the index alone does not: input 5 is a
    shared connector on an AMS8 and plain analog on an AMS24.
    """

    ANALOG = "analog"
    DIGITAL = "digital"
    #: One connector pair carrying **either** analog or digital, "but not both" -- the AMS8's
    #: inputs 5-8, which its back panel prints twice. A distinct kind rather than two entries,
    #: because choosing one forfeits the other and listing both would imply twelve inputs on an
    #: eight-input matrix.
    SHARED = "shared"


@dataclass(frozen=True, slots=True)
class MatrixSpec:
    """One matrix model: how many channels it has, and what follows from that."""

    name: str
    outputs: int
    inputs: int
    #: First input that is digital-only, 1-based. ``None`` when the model has none.
    first_digital_input: int | None = None
    #: First input whose connector is shared between analog and digital, 1-based.
    first_shared_input: int | None = None

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

    # -- connectors ---------------------------------------------------------------------------

    def input_kind(self, source: int) -> ChannelKind:
        """What terminates this input. Validates the index, so a typo cannot be labelled."""
        self.input_byte(source)
        if self.first_digital_input is not None and source >= self.first_digital_input:
            return ChannelKind.DIGITAL
        if self.first_shared_input is not None and source >= self.first_shared_input:
            return ChannelKind.SHARED
        return ChannelKind.ANALOG

    def output_kind(self, output: int) -> ChannelKind:
        """Always analog. Both models are RCA line level out; there is no digital output.

        A method rather than a constant so the setup UI can ask the same question of an output
        as of an input, and so a future model with digital outputs has one place to change.
        """
        self.output_byte(output)
        return ChannelKind.ANALOG

    # -- setup-form field names ---------------------------------------------------------------
    #
    # Here rather than in ``config_flow`` so that the key and the kind it encodes are defined
    # together, and so the test that checks every key has a label can run without importing Home
    # Assistant -- which is impossible on the Windows development box.

    def input_field(self, source: int) -> str:
        """Name of this input's checkbox in the setup form.

        **The kind is in the name because the label depends on it and labels are static.** A
        field's label comes from ``strings.json``, which cannot vary by model, and the connector
        a given index terminates in does: input 5 is a shared analog/digital pair on an AMS8 and
        plain analog on an AMS24. Keyed on the index alone, one of the two would be mislabelled.

        Only the index is ever stored, so ``active_inputs`` is unchanged and nothing migrates.
        """
        return f"input_{source}_{self.input_kind(source).value}"

    def output_field(self, output: int) -> str:
        """Name of this output's checkbox in the setup form."""
        return f"output_{output}_{self.output_kind(output).value}"

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


#: Every model this integration supports. All are square, and the installation guide lists only
#: these two -- TS-AMS8 and TS-AMS24.
#:
#: **An AMS16 used to be here and was removed 2026-08-29.** It was never measured and never
#: sourced: it entered through the design doc and propagated into this table, the README and the
#: protocol reference. The guide names two models, and the two vendor driver archives are AMS8
#: and AMS24. Offering it let someone configure a channel count no hardware has, which this file
#: exists to prevent -- ``for_model`` refuses an unknown name rather than guessing for exactly
#: that reason, and the table was quietly undermining its own rule.
MODELS: Final[dict[str, MatrixSpec]] = {
    # Inputs 5-8 are one connector pair each, analog or digital but not both.
    "AMS8": MatrixSpec(name="AMS8", outputs=8, inputs=8, first_shared_input=5),
    # 16 analog in, then 8 digital-only. Nothing is shared.
    "AMS24": MatrixSpec(name="AMS24", outputs=24, inputs=24, first_digital_input=17),
}
