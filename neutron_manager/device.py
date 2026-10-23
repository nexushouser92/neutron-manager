"""Request/reply exchanges with a Neutron over an open port (see midi_win: send, receive, drain, close).

The Neutron keeps sending tuning readings and panel changes, so every wait skips messages that are
not the expected reply; those are handed to on_other() instead of being lost.
"""
import time

from . import protocol

REPLY_TIMEOUT = 1.5


class DeviceError(Exception):
    """The Neutron did not answer, or answered something unexpected."""


class VerifyError(DeviceError):
    """A setting read back after writing is not what was sent."""


def wait_for(port, opcode, timeout=REPLY_TIMEOUT, on_other=None):
    """Payload of the next message with this opcode; other Neutron messages go to on_other(opcode, payload)."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeviceError(f"no reply {opcode} from the Neutron")
        msg = port.receive(remaining)
        if msg is None:
            raise DeviceError(f"no reply {opcode} from the Neutron")
        parsed = protocol.parse(msg)
        if parsed is None:
            continue
        got, _, payload = parsed
        if got == opcode:
            return parsed
        if on_other is not None:
            on_other(got, payload)


def read_version(port, on_other=None):
    """(comms version, firmware version tuple)."""
    port.send(protocol.version_request())
    _, comms, payload = wait_for(port, protocol.VERSION_REPLY, on_other=on_other)
    return comms, protocol.parse_version(payload)


def read_settings(port, on_other=None):
    port.send(protocol.settings_request())
    _, _, payload = wait_for(port, protocol.SETTINGS_REPLY, on_other=on_other)
    return protocol.parse_settings(payload)


def write_setting(port, key, value, old=None):
    for msg in protocol.BY_KEY[key].write_messages(value, old):
        port.send(msg)


def write_and_verify(port, settings, current, on_other=None):
    """Write every setting that differs from current, then read everything back and compare.

    Returns what the Neutron has after the write."""
    keys = settings.differences(current)
    for key in keys:
        write_setting(port, key, settings[key], None if current is None else current[key])
    after = read_settings(port, on_other)
    wrong = [k for k in keys if after[k] != settings[k]]
    if wrong:
        raise VerifyError(", ".join(f"{k}={after[k]!r} (sent {settings[k]!r})" for k in wrong))
    return after


def restore_defaults(port, on_other=None):
    port.send(protocol.restore_defaults_request())
    return read_settings(port, on_other)


def reset_action(port, pid, on_other=None):
    """Reset key range (6) or LFO shape order (57), then read the settings again."""
    if pid not in (protocol.RESET_KEY_RANGE_ID, protocol.RESET_LFO_ORDER_ID):
        raise DeviceError(f"{pid} is not a reset action")
    port.send(protocol.message(protocol.PARAM_SET, pid, 0))
    return read_settings(port, on_other)
