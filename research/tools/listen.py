"""Listen-only capture: records everything the Neutron sends for N seconds. Sends nothing."""
import sys, time
from collections import Counter
import midi_win

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30
port = midi_win.open_swing(needle="Neutron")
shorts, sysex = [], []
t0 = time.monotonic()
port.on_short = lambda s, a, b: shorts.append((time.monotonic() - t0, s, a, b))
try:
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
kinds = Counter((s & 0xF0, a) for _, s, a, _ in shorts)
print("short message types (status, data1): count", dict(kinds.most_common(40)))
for t, s, a, b in shorts[:80]:
    print(f"{t:6.2f}s {s:02X} {a:3d} {b:3d}")
