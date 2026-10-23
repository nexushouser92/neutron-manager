"""Measures how long the window blocks when switching sections and on each poll tick (no Neutron needed).
Budget for 120 fps: 8.3 ms per frame."""
import os, sys, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import customtkinter as ctk
from neutron_manager import gui, protocol
from tests.fakes import SETTINGS_REPLY
from tests.test_gui import FakeLink

root = ctk.CTk(); root.geometry("+50+50"); gui._load_fonts()
link = FakeLink(); link.linked = link.editable = True
t0 = time.perf_counter()
app = gui.App(root, lang="es", link=link, autostart=False)
root.update()
build = (time.perf_counter() - t0) * 1000
link.events.put(("settings", protocol.parse_settings(protocol.parse(SETTINGS_REPLY)[2])))
app.process_link_events(); root.update()
pages = list(gui.SETTINGS_PAGES + gui.TOOL_PAGES)
times = {p: [] for p in pages}
for _ in range(5):
    for p in pages:
        t = time.perf_counter()
        app.show_section(p)
        root.update()
        times[p].append((time.perf_counter() - t) * 1000)
print(f"build window: {build:.0f} ms")
for p in pages:
    print(f"switch to {p:12} median {statistics.median(times[p]):6.1f} ms  max {max(times[p]):6.1f} ms")
app.show_section("tuner"); root.update()
tick = []
for i in range(100):
    link.events.put(("tuning", protocol.Tuning(note=60, osc1_us=2272 + i, osc2_us=4545 - i)))
    t = time.perf_counter(); app.process_link_events(); root.update(); tick.append((time.perf_counter() - t) * 1000)
print(f"tuner tick: median {statistics.median(tick):.2f} ms  max {max(tick):.2f} ms")
app.show_section("oscillators"); root.update()
tick = []
for i in range(100):
    t = time.perf_counter(); app.process_link_events(); root.update(); tick.append((time.perf_counter() - t) * 1000)
print(f"idle tick: median {statistics.median(tick):.2f} ms  max {max(tick):.2f} ms")
root.destroy()
