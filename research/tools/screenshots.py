"""Save a screenshot of every page to the folder given as argument: linked to the real Neutron
(read-only: nothing is changed), or with --demo using settings captured from one."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import customtkinter as ctk
from PIL import ImageGrab

from neutron_manager import gui

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
ctk.set_appearance_mode("light")
root = ctk.CTk(fg_color=gui.BG)
gui._load_fonts()
lang = next((a for a in sys.argv[2:] if a in ("es", "en")), "es")
if "--demo" in sys.argv:   # no Neutron: show the settings captured from a real one, plus a tuner reading
    from neutron_manager import protocol
    from tests.fakes import SETTINGS_REPLY
    from tests.test_gui import FakeLink
    link = FakeLink()
    link.linked = link.editable = True
    app = gui.App(root, lang=lang, link=link, autostart=False)
    link.events.put(("info", (1, (2, 0, 2))))
    link.events.put(("settings", protocol.parse_settings(protocol.parse(SETTINGS_REPLY)[2])))
    link.events.put(("tuning", protocol.Tuning(note=57, osc1_us=4545, osc2_us=2299)))
    app.process_link_events()
else:
    app = gui.App(root, lang=lang)
pages = list(gui.SETTINGS_PAGES + gui.TOOL_PAGES)


def shoot(i=0):
    if i == len(pages):
        app.on_close()
        return
    app.show_section(pages[i])
    root.update()
    root.after(700, lambda: (ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                                 root.winfo_rootx() + root.winfo_width(),
                                                 root.winfo_rooty() + root.winfo_height())
                                           ).save(os.path.join(out, f"{i:02d}_{pages[i]}.png")),
                             shoot(i + 1)))


root.lift()
root.attributes("-topmost", True)
root.after(4000, shoot)
root.mainloop()
