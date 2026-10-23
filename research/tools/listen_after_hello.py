"""Sends the two read-only requests (version 115, settings 5), then only listens for N seconds."""
import sys, time
from collections import Counter
import midi_win

HEADER = bytes([0xF0, 0x00, 0x20, 0x32, 0x28, 0x7F])
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 40
port = midi_win.open_swing(needle="Neutron")
shorts, sysex = [], []
t0 = time.monotonic()
port.on_short = lambda s, a, b: shorts.append((time.monotonic() - t0, s, a, b))
try:
    for op in (115, 5):
        port.send(HEADER + bytes([op, 0xF7]))
    print("escuchando", flush=True)
    while time.monotonic() - t0 < secs:
        m = port.receive(0.2)
        if m:
            sysex.append((time.monotonic() - t0, m))
finally:
    port.close()
ops = Counter(m[6] for _, m in sysex if len(m) > 7)
print(f"listened {secs:.0f} s | sysex by opcode: {dict(ops)} | short messages: {len(shorts)}")
for t, m in sysex:
    if len(m) > 7 and m[6] != 0x72:
        print(f"{t:6.2f}s sysex {m.hex(' ')}")
tun = [m for _, m in sysex if len(m) > 7 and m[6] == 0x72]
if tun:
    print("first/last tuning:", tun[0].hex(' '), "|", tun[-1].hex(' '))
kinds = Counter((s & 0xF0, a) for _, s, a, _ in shorts)
print("short (status, data1): count", dict(kinds.most_common(40)))
for t, s, a, b in shorts[:80]:
    print(f"{t:6.2f}s {s:02X} {a:3d} {b:3d}")
