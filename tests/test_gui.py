import queue
import unittest

import customtkinter as ctk

from neutron_manager import gui, protocol
from tests import fakes


class FakeLink:
    def __init__(self):
        self.events = queue.Queue()
        self.linked = False
        self.editable = False
        self.has_pending = False
        self.pushed = []
        self.actions = []

    def push(self, settings):
        self.pushed.append(settings)

    def flush(self):
        pass

    def request_read(self):
        pass

    def request_action(self, name):
        self.actions.append(name)

    def stop(self):
        pass


class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = ctk.CTk()
        except Exception as e:   # no display
            raise unittest.SkipTest(f"no Tk display: {e}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.link = FakeLink()
        self.answers = []
        self.app = gui.App(self.root, lang="es", link=self.link, autostart=False,
                           ask_yes_no=lambda *a: self.answers.pop(0) if self.answers else True)

    def tearDown(self):
        self.app.destroy()

    def link_with(self, settings):
        self.link.linked = self.link.editable = True
        self.link.events.put(("linked", None))
        self.link.events.put(("info", (1, (2, 0, 2))))
        self.link.events.put(("settings", settings))
        self.app.process_link_events()

    def test_shows_settings_and_pushes_changes(self):
        settings = protocol.parse_settings(protocol.parse(fakes.SETTINGS_REPLY)[2])
        self.link_with(settings)
        self.assertEqual(self.app.settings, settings)
        self.assertEqual(self.app.status_button.cget("text"), self.app.t("status_linked"))
        self.app.set_value("vcf_mode", 0)
        self.assertEqual(self.link.pushed[-1]["vcf_mode"], 0)
        self.app.controls["lfo_shape_order"]._set_element(0, 4)
        self.assertEqual(self.link.pushed[-1]["lfo_shape_order"], (4, 1, 2, 3, 4))
        self.app.refresh()
        seg = self.app.controls["vcf_mode"].widgets[0]
        self.assertEqual(seg.index, 0)                       # the segmented control shows what was set
        self.app.controls["osc_sync"]._pick(self.app.t("on"))
        self.assertTrue(self.link.pushed[-1]["osc_sync"])

    def test_every_page_builds_and_switches(self):
        self.link_with(protocol.Settings())
        for section in gui.SETTINGS_PAGES + gui.TOOL_PAGES:
            self.app.show_section(section)
            self.assertEqual(self.app._shown["page"], self.app.pages[section])

    def test_outdated_firmware_shows_a_message(self):
        self.link.linked = True
        self.link.events.put(("linked", None))
        self.link.events.put(("outdated", (1, 2, 0)))
        self.app.process_link_events()
        self.assertIsNone(self.app.settings)
        self.assertIn("1.2.0", self.app.status_button.cget("text"))

    def test_tuner_shows_note_and_cents(self):
        tuning = protocol.Tuning(note=69, osc1_us=round(1e6 / 440), osc2_us=round(1e6 / 220))
        self.link.events.put(("tuning", tuning))
        self.app.process_link_events()
        self.assertEqual(self.app.tuner_note_text(0), "A3")
        self.assertEqual(self.app.tuner_note_text(1), "A2")

    def test_nearest_note(self):
        self.assertEqual(gui.nearest_note(440.0)[0], 69)
        note, cents = gui.nearest_note(440.0 * 2 ** (10 / 1200))
        self.assertEqual(note, 69)
        self.assertAlmostEqual(cents, 10.0, places=6)

    def test_restore_defaults_asks_first(self):
        self.link_with(protocol.Settings())
        self.answers = [False]
        self.app.restore_defaults()
        self.assertEqual(self.link.actions, [])
        self.answers = [True]
        self.app.restore_defaults()
        self.assertEqual(self.link.actions, ["restore_defaults"])

    def test_firmware_buttons_need_bootloader_and_backup(self):
        self.app.refresh()
        self.assertEqual(self.app.fw_buttons["fw_backup"].cget("state"), "disabled")
        self.app.boot = object()
        self.app.refresh()
        self.assertEqual(self.app.fw_buttons["fw_backup"].cget("state"), "normal")
        self.assertEqual(self.app.fw_buttons["fw_install"].cget("state"), "disabled")


if __name__ == "__main__":
    unittest.main()
