import os
import struct
import unittest

from neutron_manager import dfu, firmware
from tests import fakes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def vector_image(size, address=firmware.FLASH_BASE, fill=0x5A):
    data = bytearray([fill]) * size
    struct.pack_into("<II", data, 0, 0x20004000, firmware.FLASH_BASE + 0xC1)
    return firmware.Image(address, bytes(data))


class Files(unittest.TestCase):
    def test_r15f_files_agree(self):
        paths = [os.path.join(ROOT, n) for n in ("r15f_08000000.bin", "r15f.hex", "r15f.dfu")]
        if not all(os.path.exists(p) for p in paths):
            self.skipTest("r15f files not present")
        images = [firmware.load(p) for p in paths]
        self.assertEqual({i.sha256 for i in images}, {images[0].sha256})
        self.assertEqual(len(images[0].data), 78436)
        self.assertEqual(images[0].pages(), list(range(39)))

    def test_hex_checksum_and_gaps(self):
        text = ":0400000001020304F2\n:02000800AABB91\n:00000001FF\n"
        image = firmware.read_hex(text)
        self.assertEqual(image.data, bytes([1, 2, 3, 4, 0xFF, 0xFF, 0xFF, 0xFF, 0xAA, 0xBB]))
        with self.assertRaises(firmware.FirmwareError):
            firmware.read_hex(":0400000001020304F3\n")

    def test_dfu_crc_is_checked(self):
        path = os.path.join(ROOT, "r15f.dfu")
        if not os.path.exists(path):
            self.skipTest("r15f.dfu not present")
        with open(path, "rb") as f:
            blob = bytearray(f.read())
        blob[400] ^= 1
        with self.assertRaises(firmware.FirmwareError):
            firmware.read_dfu(bytes(blob))

    def test_rejects_images_that_do_not_fit(self):
        with self.assertRaises(firmware.FirmwareError):
            vector_image(firmware.FLASH_SIZE + 4).check_fits()
        with self.assertRaises(firmware.FirmwareError):
            firmware.Image(firmware.FLASH_BASE, b"\xFF" * 64).check_fits()   # no vector table
        with self.assertRaises(firmware.FirmwareError):
            vector_image(64, firmware.FLASH_BASE + 100).check_fits()


class Dfu(unittest.TestCase):
    def boot(self, fake=None, layout=fakes.FakeBootloader.LAYOUT):
        self.fake = fake or fakes.FakeBootloader()
        return dfu.Bootloader(self.fake, layout, self.fake.transfer, sleep=lambda s: None)

    def test_layout_must_be_the_neutron_chip(self):
        self.assertEqual(dfu.parse_layout(fakes.FakeBootloader.LAYOUT), (0x08000000, 64, 2048))
        with self.assertRaises(dfu.DfuError):
            self.boot(layout="@Internal Flash  /0x08000000/128*002Kg")
        with self.assertRaises(dfu.DfuError):
            dfu.parse_layout("garbage")

    def test_backup_reads_the_whole_flash(self):
        b = self.boot()
        image = b.backup()
        self.assertEqual(image.data, bytes(self.fake.flash))
        self.assertEqual(len(image.data), firmware.FLASH_SIZE)

    def test_flash_needs_a_backup_from_this_session(self):
        b = self.boot()
        with self.assertRaises(dfu.DfuError):
            b.flash(vector_image(4096), None)
        other = firmware.Image(firmware.FLASH_BASE, b"\x01" * firmware.FLASH_SIZE)
        b.backup()
        with self.assertRaises(dfu.DfuError):
            b.flash(vector_image(4096), other)
        self.assertEqual(self.fake.erased, [])

    def test_flash_erases_only_its_pages_and_keeps_the_rest(self):
        b = self.boot()
        backup = b.backup()
        image = vector_image(5000)
        b.flash(image, backup)
        self.assertFalse(self.fake.mass_erased)
        self.assertEqual(self.fake.erased, [firmware.FLASH_BASE + i * 2048 for i in range(3)])
        self.assertEqual(bytes(self.fake.flash[:5000]), image.data)
        self.assertEqual(bytes(self.fake.flash[6144:]), backup.data[6144:])   # pages 3..63 untouched
        self.assertEqual(bytes(self.fake.flash[5000:6144]), b"\xFF" * 1144)   # rest of the last page erased

    def test_restore_full_backup(self):
        b = self.boot()
        backup = b.backup()
        b.flash(vector_image(5000), backup)
        b.flash(backup, backup)
        self.assertEqual(bytes(self.fake.flash), backup.data)

    def test_blank_backup_is_refused(self):
        b = self.boot(fakes.FakeBootloader(readable=False))
        with self.assertRaises(dfu.DfuError):
            b.backup()

    def test_verification_failure_is_reported(self):
        b = self.boot()
        backup = b.backup()
        real_execute = self.fake._execute

        def corrupt():
            real_execute()
            if self.fake.writes:
                self.fake.flash[10] ^= 0xFF
        self.fake._execute = corrupt
        with self.assertRaises(dfu.DfuError):
            b.flash(vector_image(4096), backup)

    def test_leave_starts_the_firmware(self):
        b = self.boot()
        b.leave()
        self.assertTrue(self.fake.left)
        self.assertEqual(self.fake.pointer, firmware.FLASH_BASE)


if __name__ == "__main__":
    unittest.main()
