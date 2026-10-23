"""Read-only probe of a connected Neutron: asks for the firmware version (opcode 115) and the global
settings (opcode 5), and prints every SysEx and short message it receives. Writes nothing.

python research/tools/probe_read.py [seconds_to_listen]
"""
import sys
import time

import midi_win

HEADER = bytes([0xF0, 0x00, 0x20, 0x32, 0x28, 0x7F])
READ_ONLY = {5, 115}


def request(port, opcode):
    assert opcode in READ_ONLY, "this probe only sends read requests"
    port.send(HEADER + bytes([opcode, 0xF7]))


def hexs(data):
    return " ".join(f"{b:02X}" for b in data)


def main():
    listen = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    port = midi_win.open_swing(needle="Neutron")
    shorts = []
    port.on_short = lambda s, d1, d2: shorts.append((time.monotonic(), s, d1, d2))
    try:
        for opcode in (115, 5):
            request(port, opcode)
            reply = port.receive(1.5)
            print(f"request {opcode}: {hexs(reply) if reply else 'no reply'}")
        end = time.monotonic() + listen
        while time.monotonic() < end:
            msg = port.receive(0.2)
            if msg:
                print(f"sysex: {hexs(msg)}")
        print(f"short messages in {listen:.0f} s: {len(shorts)}")
        for _, s, d1, d2 in shorts[:200]:
            print(f"  {s:02X} {d1:3d} {d2:3d}")
    finally:
        port.close()


if __name__ == "__main__":
    main()
