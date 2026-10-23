"""Which read-only request starts the 50-reading tuning burst, and how long it lasts. Sends only 5 / 115."""
import time
import midi_win

HEADER = bytes([0xF0, 0x00, 0x20, 0x32, 0x28, 0x7F])
port = midi_win.open_swing(needle="Neutron")


def run(label, opcode, secs=6):
    port.drain()
    t0 = time.monotonic()
    if opcode is not None:
        port.send(HEADER + bytes([opcode, 0xF7]))
    times = []
    while time.monotonic() - t0 < secs:
        m = port.receive(0.1)
        if m and len(m) > 7 and m[6] == 0x72:
            times.append(time.monotonic() - t0)
    span = f"{times[0]:.2f}-{times[-1]:.2f}s" if times else "-"
    print(f"{label:28} tuning readings: {len(times):3d}  span {span}")


try:
    run("nothing sent", None, 3)
    run("version request (115)", 115)
    run("settings request (5)", 5)
    run("settings request (5) again", 5)
finally:
    port.close()
