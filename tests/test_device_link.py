import unittest

from neutron_manager import device, link, protocol
from tests import fakes


class Device(unittest.TestCase):
    def test_reads_version_and_settings_past_tuning_messages(self):
        port = fakes.FakeNeutron(stream_tuning=3)
        seen = []
        device.read_settings(port)           # leaves a burst of tuning readings waiting
        comms, version = device.read_version(port, lambda op, payload: seen.append(op))
        self.assertEqual((comms, version), (1, (2, 0, 2)))
        self.assertEqual(seen, [protocol.TUNING_REPLY] * 3)
        settings = device.read_settings(port, lambda op, payload: seen.append(op))
        self.assertEqual(settings["osc2_range"], 2)

    def test_write_and_verify_round_trip(self):
        port = fakes.FakeNeutron()
        current = device.read_settings(port)
        wanted = (current.set("osc_sync", True).set("key_range_min", 36).set("key_range_max", 84)
                  .set("osc_key_split", 60).set("lfo_shape_order", (4, 3, 2, 1, 0)).set("midi_channel", 9))
        after = device.write_and_verify(port, wanted, current)
        self.assertEqual(after, wanted)
        self.assertEqual(device.read_settings(port), wanted)

    def test_paraphonic_priority_uses_the_shared_number(self):
        port = fakes.FakeNeutron()
        current = device.read_settings(port)
        para = device.write_and_verify(port, current.set("paraphonic", True), current)
        after = device.write_and_verify(port, para.set("para_note_priority", 3), para)
        self.assertEqual(after["para_note_priority"], 3)
        self.assertEqual(after["note_priority"], 2)

    def test_verify_catches_a_setting_not_kept(self):
        port = fakes.FakeNeutron(ignore={protocol.BY_KEY["midi_channel"].pid})
        current = device.read_settings(port)
        with self.assertRaises(device.VerifyError):
            device.write_and_verify(port, current.set("midi_channel", 3), current)

    def test_no_reply_is_an_error(self):
        port = fakes.FakeNeutron()
        port.send = lambda data: None
        with self.assertRaises(device.DeviceError):
            device.read_settings(port)

    def test_resets_and_defaults(self):
        port = fakes.FakeNeutron()
        current = device.read_settings(port)
        device.write_and_verify(port, current.set("key_range_min", 48).set("lfo_shape_order", (1, 0, 2, 3, 4)),
                                current)
        self.assertEqual(device.reset_action(port, protocol.RESET_KEY_RANGE_ID)["key_range_min"], 24)
        self.assertEqual(device.reset_action(port, protocol.RESET_LFO_ORDER_ID)["lfo_shape_order"],
                         (0, 1, 2, 3, 4))
        with self.assertRaises(device.DeviceError):
            device.reset_action(port, 10)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Link(unittest.TestCase):
    def make(self, neutron=None, present=True):
        self.neutron = neutron or fakes.FakeNeutron(stream_tuning=1)
        self.clock = FakeClock()
        self.present = present
        names = (["Neutron(1)"], ["Neutron(1)"])
        lister = lambda: names if self.present else ([], [])
        return link.NeutronLink(opener=lambda: self.neutron, lister=lister, clock=self.clock)

    def events(self, l):
        out = []
        while not l.events.empty():
            out.append(l.events.get_nowait())
        return out

    def test_links_and_reads(self):
        l = self.make()
        l.step()
        kinds = [k for k, _ in self.events(l)]
        self.assertEqual(kinds, ["linked", "info", "settings"])
        self.assertTrue(l.editable)

    def test_idle_polling_keeps_the_tuner_alive(self):
        l = self.make()
        l.step()
        self.events(l)
        for _ in range(4):           # first poll goes out, then its reply and the burst are read
            l.step()
        self.assertIn(protocol.settings_request(), self.neutron.sent[2:])
        self.assertIn("tuning", [k for k, _ in self.events(l)])
        self.assertIsNone(l._poll_sent)

    def test_poll_reports_changes_made_on_the_panel(self):
        l = self.make()
        l.step()
        self.events(l)
        self.neutron.words[0] |= 1 << 4        # OSC SYNC pressed on the Neutron
        for _ in range(3):
            l.step()
        settings = [p for k, p in self.events(l) if k == "settings"]
        self.assertEqual(len(settings), 1)
        self.assertTrue(settings[0]["osc_sync"])

    def test_poll_without_changes_is_silent(self):
        l = self.make()
        l.step()
        self.events(l)
        for _ in range(3):
            l.step()
        self.assertNotIn("settings", [k for k, _ in self.events(l)])

    def test_write_waits_for_an_outstanding_poll_reply(self):
        l = self.make(fakes.FakeNeutron())
        l.step()
        l.step()                                  # poll sent, reply not read yet
        self.assertIsNotNone(l._poll_sent)
        l.push(l.on_neutron.set("vcf_mode", 1))
        self.clock.now += 0.4                     # write is due, but the poll reply comes first
        sent_before = len(self.neutron.sent)
        l.step()
        self.assertEqual(len(self.neutron.sent), sent_before)
        self.assertIsNone(l._poll_sent)
        l.step()
        self.assertEqual(l.on_neutron["vcf_mode"], 1)

    def test_outdated_firmware_is_not_edited(self):
        l = self.make(fakes.FakeNeutron(version=b"1.2.0"))
        l.step()
        kinds = [k for k, _ in self.events(l)]
        self.assertIn(("outdated"), kinds)
        self.assertNotIn("settings", kinds)
        self.assertFalse(l.editable)

    def test_push_writes_after_delay(self):
        l = self.make()
        l.step()
        self.events(l)
        l.push(l.on_neutron.set("vcf_mode", 0))
        l.step()
        self.assertNotIn("written", [k for k, _ in self.events(l)])
        self.clock.now += 1
        l.step()
        events = self.events(l)
        self.assertIn("written", [k for k, _ in events])
        self.assertEqual(l.on_neutron["vcf_mode"], 0)
        self.assertFalse(l.has_pending)

    def test_panel_change_makes_it_read_again(self):
        l = self.make()
        l.step()
        self.events(l)
        self.neutron.inbox.put(protocol.message(protocol.PARAM_CHANGED, 14, 1))
        sent_before = len(self.neutron.sent)
        for _ in range(5):   # poll goes out, the change message and the poll reply are read, then a full read
            l.step()
        self.assertIn("settings", [k for k, _ in self.events(l)])
        self.assertEqual(self.neutron.sent[sent_before:], [protocol.settings_request()] * 2)

    def test_unplug_drops_and_relinks(self):
        l = self.make()
        l.step()
        self.present = False
        self.clock.now += 2
        l.step()
        self.assertFalse(l.linked)
        self.assertTrue(self.neutron.closed)
        self.present = True
        self.clock.now += 2
        l.step()
        self.assertTrue(l.linked)

    def test_actions(self):
        l = self.make()
        l.step()
        l.request_action("restore_defaults")
        l.step()
        self.assertIn(protocol.restore_defaults_request(), self.neutron.sent)
        self.assertIn("written", [k for k, _ in self.events(l)])


if __name__ == "__main__":
    unittest.main()
