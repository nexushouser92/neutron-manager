"""Firmware images: read .bin, Intel .hex and DfuSe .dfu files into one checked, contiguous image.

The Neutron's microcontroller (STM32F072CB) has 128 KB of flash at 0x08000000 in 2 KB pages.
"""
import hashlib
import os
import struct
import zlib
from dataclasses import dataclass

FLASH_BASE = 0x08000000
FLASH_SIZE = 128 * 1024
PAGE_SIZE = 2048
BIN_SUFFIX_ADDRESS = "_08000000"


class FirmwareError(Exception):
    """A firmware file that cannot be read or does not fit the Neutron."""


@dataclass(frozen=True)
class Image:
    address: int
    data: bytes

    @property
    def end(self):
        return self.address + len(self.data)

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()

    def pages(self):
        """Indexes of the flash pages this image touches."""
        first = (self.address - FLASH_BASE) // PAGE_SIZE
        last = (self.end - 1 - FLASH_BASE) // PAGE_SIZE
        return list(range(first, last + 1))

    def check_fits(self):
        if self.address < FLASH_BASE or self.end > FLASH_BASE + FLASH_SIZE:
            raise FirmwareError(f"image 0x{self.address:08X}-0x{self.end:08X} is outside the Neutron's flash")
        if (self.address - FLASH_BASE) % PAGE_SIZE:
            raise FirmwareError("image does not start on a flash page")
        if len(self.data) < 8:
            raise FirmwareError("image is empty")
        stack, reset = struct.unpack_from("<II", self.data)
        if self.address == FLASH_BASE and not (0x20000000 < stack <= 0x20004000 and FLASH_BASE <= reset < FLASH_BASE + FLASH_SIZE):
            raise FirmwareError("this does not look like Neutron firmware (bad vector table)")
        return self


def _join(segments):
    """[(address, bytes)] -> one Image, gaps filled with 0xFF (erased flash)."""
    if not segments:
        raise FirmwareError("no data in file")
    segments = sorted(segments)
    start = segments[0][0]
    end = max(a + len(d) for a, d in segments)
    buf = bytearray(b"\xFF" * (end - start))
    for address, data in segments:
        buf[address - start:address - start + len(data)] = data
    return Image(start, bytes(buf))


def read_hex(text):
    segments, base = [], 0
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise FirmwareError(f"line {number}: not Intel HEX")
        raw = bytes.fromhex(line[1:])
        if sum(raw) & 0xFF:
            raise FirmwareError(f"line {number}: bad checksum")
        count, offset, kind = raw[0], int.from_bytes(raw[1:3], "big"), raw[3]
        data = raw[4:4 + count]
        if kind == 0:
            segments.append((base + offset, data))
        elif kind == 1:
            break
        elif kind == 2:
            base = int.from_bytes(data, "big") << 4
        elif kind == 4:
            base = int.from_bytes(data, "big") << 16
    return _join(segments)


def dfu_crc(data):
    """CRC of a DFU file suffix: CRC-32 without the final inversion."""
    return (~zlib.crc32(data)) & 0xFFFFFFFF


def read_dfu(blob):
    if blob[:5] != b"DfuSe":
        raise FirmwareError("not a DfuSe file")
    if len(blob) < 11 + 16 or blob[-8:-5] != b"UFD":
        raise FirmwareError("DfuSe file has no DFU suffix")
    if struct.unpack_from("<I", blob, len(blob) - 4)[0] != dfu_crc(blob[:-4]):
        raise FirmwareError("DfuSe file is damaged (CRC does not match)")
    targets = blob[10]
    pos, segments = 11, []
    for _ in range(targets):
        if blob[pos:pos + 6] != b"Target":
            raise FirmwareError("DfuSe target header missing")
        elements = struct.unpack_from("<I", blob, pos + 270)[0]
        pos += 274
        for _ in range(elements):
            address, size = struct.unpack_from("<II", blob, pos)
            segments.append((address, blob[pos + 8:pos + 8 + size]))
            pos += 8 + size
    return _join(segments)


def read_bin(blob, address=FLASH_BASE):
    return Image(address, bytes(blob))


def load(path):
    """Read a firmware file; the format comes from its extension. Raises FirmwareError."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError as e:
        raise FirmwareError(str(e)) from e
    if ext == ".hex":
        image = read_hex(blob.decode("ascii", "replace"))
    elif ext == ".dfu":
        image = read_dfu(blob)
    elif ext == ".bin":
        image = read_bin(blob)
    else:
        raise FirmwareError(f"unknown firmware file type {ext}")
    return image.check_fits()
