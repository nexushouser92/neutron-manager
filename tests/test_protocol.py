import unittest

from neutron_manager import protocol as p
from tests import fakes


class Packing(unittest.TestCase):
    def test_round_trip(self):
        for n in range(0, 30):
            data = bytes((i * 37 + 200) & 0xFF for i in range(n))
            packed = p.pack7(data)
            self.assertTrue(all(b < 0x80 for b in packed))
            self.assertEqual(p.unpack7(packed)[:n], data)

    def test_full_group_gets_an_extra_group(self):
        self.assertEqual(len(p.pack7(bytes(7))), 16)
        self.assertEqual(len(p.pack7(bytes(20))), 24)

    def test_high_bits_byte_follows_the_group(self):
        self.assertEqual(p.pack7(bytes([0x80, 0, 0x81])), bytes([0, 0, 1, 0, 0, 0, 0, 0b101]))

    def test_rejects_bad_packed_data(self):
        with self.assertRaises(p.ProtocolError):
            p.unpack7(bytes(7))
        with self.assertRaises(p.ProtocolError):
            p.unpack7(bytes([0x80] + [0] * 7))


class Messages(unittest.TestCase):
    def test_requests_match_the_spec(self):
        self.assertEqual(p.version_request().hex(" "), "f0 00 20 32 28 7f 73 f7")
        self.assertEqual(p.settings_request().hex(" "), "f0 00 20 32 28 7f 05 f7")
        self.assertEqual(p.restore_defaults_request().hex(" "), "f0 00 20 32 28 7f 0b f7")

    def test_parse_rejects_other_messages(self):
        self.assertIsNone(p.parse(b"\xF0\x00\x20\x32\x29\x00\x06\x01\xF7"))
        self.assertIsNone(p.parse(b"\x90\x3C\x40"))

    def test_captured_version(self):
        opcode, comms, payload = p.parse(fakes.VERSION_REPLY)
        self.assertEqual((opcode, comms), (p.VERSION_REPLY, 1))
        self.assertEqual(p.parse_version(payload), (2, 0, 2))

    def test_version_forms_and_minimum(self):
        self.assertEqual(p.parse_version(b"v2.02"), (2, 0, 2))
        self.assertTrue(p.version_supported((2, 0, 2)))
        self.assertTrue(p.version_supported((0, 9, 1)))
        self.assertFalse(p.version_supported((2, 0, 1)))
        self.assertFalse(p.version_supported((1, 9, 9)))

    def test_captured_tuning(self):
        tuning = p.parse_tuning(p.parse(fakes.TUNING_REPLY)[2])
        self.assertEqual((tuning.note, tuning.osc1_us, tuning.osc2_us), (0, 2446, 5592))
        self.assertAlmostEqual(tuning.osc1_hz, 408.83, places=2)


class SettingsDecoding(unittest.TestCase):
    def setUp(self):
        self.s = p.parse_settings(p.parse(fakes.SETTINGS_REPLY)[2])

    def test_captured_settings(self):
        expected = {
            "osc1_range": 1, "osc2_range": 2, "vcf_mode": 2, "assign_out": 0, "midi_channel": 0,
            "note_priority": 2, "para_note_priority": 2, "pitch_bend_range": 2, "osc1_autoglide": 12,
            "osc2_autoglide": 12, "lfo_shape_order": (0, 1, 2, 3, 4), "lfo_phase_offset": (0, 0, 0, 0, 0),
            "key_range_min": 24, "key_range_max": 96, "osc_key_split": 0, "lfo_key_track": 0,
            "vcf_mod_depth": 63, "lfo_depth": 63, "paraphonic": False, "osc_sync": False,
        }
        for key, value in expected.items():
            self.assertEqual(self.s[key], value, key)

    def test_transforms(self):
        split = p.BY_KEY["osc_key_split"]
        self.assertEqual(split.decode(1 << 10), 24)
        self.assertEqual(split.decode(63 << 10), 86)
        self.assertEqual(p.BY_KEY["key_range_max"].decode(63 << 6), 33)
        self.assertEqual(p.BY_KEY["key_range_min"].decode(63), 87)

    def test_every_field_fits_its_word(self):
        used = {}
        for param in p.PARAMS:
            self.assertLessEqual(param.offset + param.width, 16, param.key)
            for bit in range(param.offset, param.offset + param.width):
                self.assertNotIn((param.block, bit), used, f"{param.key} overlaps {used.get((param.block, bit))}")
                used[(param.block, bit)] = param.key


class Writing(unittest.TestCase):
    def test_bool_choice_and_note(self):
        self.assertEqual(p.BY_KEY["osc_sync"].write_messages(True), [p.message(10, 14, 1)])
        self.assertEqual(p.BY_KEY["vcf_mode"].write_messages(0), [p.message(10, 16, 0)])
        self.assertEqual(p.BY_KEY["key_range_min"].write_messages(60), [p.message(10, 12, 60)])

    def test_array_sends_only_changed_elements(self):
        msgs = p.BY_KEY["lfo_shape_order"].write_messages((4, 1, 2, 3, 0), (0, 1, 2, 3, 4))
        self.assertEqual(msgs, [p.message(10, 56, 0, 4), p.message(10, 56, 4, 0)])

    def test_rejects_values_outside_the_panel(self):
        for key, value in (("midi_channel", 16), ("osc1_range", 4), ("key_range_max", 20),
                           ("osc_key_split", 10), ("osc_sync", 1), ("lfo_shape_order", (0, 1, 2))):
            with self.assertRaises(p.ProtocolError, msg=key):
                p.BY_KEY[key].write_messages(value)

    def test_shared_number_follows_paraphonic_mode(self):
        self.assertEqual(p.param_for_pid(1, False).key, "note_priority")
        self.assertEqual(p.param_for_pid(1, True).key, "para_note_priority")
        self.assertIsNone(p.param_for_pid(99, False))


class SettingsObject(unittest.TestCase):
    def test_set_is_immutable_and_checked(self):
        a = p.Settings()
        b = a.set("midi_channel", 5)
        self.assertEqual(a["midi_channel"], 0)
        self.assertEqual(b.differences(a), ["midi_channel"])
        with self.assertRaises(p.ProtocolError):
            a.set("nope", 1)

    def test_dict_round_trip(self):
        s = p.Settings().set("lfo_phase_offset", (1, 2, 3, 4, 5)).set("osc_sync", True)
        self.assertEqual(p.Settings.from_dict(s.to_dict()), s)

    def test_note_names(self):
        self.assertEqual(p.note_name(60), "C3")
        self.assertEqual(p.note_name(24), "C0")
        self.assertEqual(p.note_name(0), "C-2")


if __name__ == "__main__":
    unittest.main()
