"""Failures the device client can raise.

The split between ``CommandError`` and ``TransportError`` is the important one. The matrix
answers ``Command error`` -- and sometimes an empty frame -- on a perfectly healthy socket, so
treating those as connection failures produces a reconnect loop that makes the integration worse
under exactly the conditions it should ride out.
"""

from __future__ import annotations


class TriadError(Exception):
    """Base class for every failure raised by this client."""


class TransportError(TriadError):
    """The socket failed. Reconnecting is the appropriate response."""


class CommandError(TriadError):
    """The device rejected the command, or returned an empty frame.

    Application-layer and usually transient. Retry the command; do not drop the connection.
    """


class ParseError(TriadError):
    """A response arrived that this client does not understand.

    Distinct from ``CommandError`` because it means the firmware speaks a dialect this client has
    not been taught, which retrying will not fix. Raised rather than defaulted: a default here
    would report a silent zone as playing at full volume.
    """
