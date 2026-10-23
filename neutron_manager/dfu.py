"""USB DFU with ST's DfuSe extensions, for the STM32 ROM bootloader inside the Neutron (0483:DF11).

Written from the public specs (USB DFU 1.1 and ST AN3156). Safety rules built in:
- the device must report the expected flash layout (STM32F072, 64 pages of 2 KB) or nothing is done;
- only the pages an image covers are erased, never the whole chip, and read protection is never touched;
- every write is read back and compared;
- flashing refuses to start without a full backup made from this same chip in this session.

USB access goes through a transport with ctrl_out(request, value, data) and ctrl_in(request, value,
length), so the logic can be tested without hardware.
"""
import re
import struct
import time

from . import firmware

VID, PID = 0x0483, 0xDF11

DNLOAD, UPLOAD, GETSTATUS, CLRSTATUS, GETSTATE, ABORT = 1, 2, 3, 4, 5, 6
CMD_SET_ADDRESS, CMD_ERASE = 0x21, 0x41

STATE_IDLE = 2
STATE_DNLOAD_SYNC, STATE_DNBUSY, STATE_DNLOAD_IDLE = 3, 4, 5
STATE_MANIFEST = 7
STATE_UPLOAD_IDLE = 9
STATE_ERROR = 10

EXPECTED_LAYOUT = (firmware.FLASH_BASE, 64, firmware.PAGE_SIZE)
DEFAULT_TRANSFER = 2048


class DfuError(Exception):
    """The bootloader is missing, refused a request, or a check failed. Flash is never left half-checked."""


def parse_layout(name):
    """'@Internal Flash  /0x08000000/64*002Kg' -> (0x08000000, 64, 2048) for the first sector group."""
    m = re.search(r"/0x([0-9A-Fa-f]+)/(\d+)\*(\d+)\s*([KMB ])", name or "")
    if not m:
        raise DfuError(f"unknown memory layout {name!r}")
    unit = {"K": 1024, "M": 1024 * 1024}.get(m.group(4).upper(), 1)
    return int(m.group(1), 16), int(m.group(2)), int(m.group(3)) * unit


class Bootloader:
    def __init__(self, transport, layout_name, transfer_size=DEFAULT_TRANSFER, sleep=time.sleep):
        layout = parse_layout(layout_name)
        if layout != EXPECTED_LAYOUT:
            raise DfuError(f"this is not a Neutron (STM32F072) bootloader: {layout_name!r}")
        self.t = transport
        self.transfer = transfer_size
        self.sleep = sleep
        self.backup_sha = None       # SHA-256 of the full backup taken in this session

    # ------------------------------------------------------------ low level
    def status(self):
        data = bytes(self.t.ctrl_in(GETSTATUS, 0, 6))
        if len(data) < 6:
            raise DfuError("short status reply")
        error, poll = data[0], data[1] | data[2] << 8 | data[3] << 16
        return error, poll, data[4]

    def wait_idle(self, want):
        for _ in range(200):
            error, poll, state = self.status()
            if state == STATE_ERROR or error:
                self.t.ctrl_out(CLRSTATUS, 0, b"")
                raise DfuError(f"bootloader reported error {error}")
            if state == want:
                return
            self.sleep(max(poll, 1) / 1000)
        raise DfuError("bootloader did not finish in time")

    def to_idle(self):
        error, _, state = self.status()
        if state == STATE_ERROR or error:
            self.t.ctrl_out(CLRSTATUS, 0, b"")
        elif state != STATE_IDLE:
            self.t.ctrl_out(ABORT, 0, b"")
        _, _, state = self.status()
        if state != STATE_IDLE:
            raise DfuError(f"bootloader is not idle (state {state})")

    def _command(self, cmd, address):
        self.t.ctrl_out(DNLOAD, 0, bytes([cmd]) + struct.pack("<I", address))
        self.wait_idle(STATE_DNLOAD_IDLE)

    def set_address(self, address):
        self._command(CMD_SET_ADDRESS, address)

    def erase_page(self, address):
        if (address - firmware.FLASH_BASE) % firmware.PAGE_SIZE:
            raise DfuError("erase address is not a page start")
        self._command(CMD_ERASE, address)

    # ------------------------------------------------------------ operations
    def read(self, address, length, progress=None):
        self.to_idle()
        self.set_address(address)
        self.to_idle()
        out = bytearray()
        block = 2
        while len(out) < length:
            chunk = bytes(self.t.ctrl_in(UPLOAD, block, self.transfer))
            if not chunk:
                raise DfuError("empty read from bootloader")
            out += chunk
            block += 1
            if progress:
                progress(min(len(out), length), length)
        self.to_idle()
        return bytes(out[:length])

    def backup(self, progress=None):
        """Read the whole flash (128 KB). Remembered so that flash() is allowed afterwards."""
        data = self.read(firmware.FLASH_BASE, firmware.FLASH_SIZE, progress)
        if data == b"\xFF" * len(data) or data == b"\x00" * len(data):
            raise DfuError("the backup is blank: the chip may be read-protected; nothing will be written")
        image = firmware.Image(firmware.FLASH_BASE, data)
        self.backup_sha = image.sha256
        return image

    def flash(self, image, backup, progress=None):
        """Erase only the pages image covers, write it, read it back and compare."""
        image.check_fits()
        if self.backup_sha is None or backup is None or backup.sha256 != self.backup_sha:
            raise DfuError("make a full backup of this Neutron first")
        pages = image.pages()
        total = len(pages) + 2 * len(image.data)
        done = 0
        self.to_idle()
        for page in pages:
            self.erase_page(firmware.FLASH_BASE + page * firmware.PAGE_SIZE)
            done += 1
            if progress:
                progress(done, total)
        self.set_address(image.address)
        for i, start in enumerate(range(0, len(image.data), self.transfer)):
            chunk = image.data[start:start + self.transfer]
            self.t.ctrl_out(DNLOAD, i + 2, chunk)
            self.wait_idle(STATE_DNLOAD_IDLE)
            done += len(chunk)
            if progress:
                progress(done, total)
        self.to_idle()
        written = self.read(image.address, len(image.data),
                            (lambda n, _: progress(done + n, total)) if progress else None)
        if written != image.data:
            raise DfuError("verification failed: what was read back differs from the file. "
                           "Do not unplug; write the backup again.")

    def leave(self):
        """Start the firmware at 0x08000000 (the device leaves DFU mode and disconnects)."""
        self.to_idle()
        self.set_address(firmware.FLASH_BASE)
        self.t.ctrl_out(DNLOAD, 2, b"")
        try:
            self.status()
        except Exception:
            pass   # the bootloader resets while answering; that is the expected end


# ------------------------------------------------------------ real USB (pyusb + libusb)
class UsbTransport:
    def __init__(self, dev, interface):
        self.dev, self.interface = dev, interface

    def ctrl_out(self, request, value, data):
        self.dev.ctrl_transfer(0x21, request, value, self.interface, data, timeout=5000)

    def ctrl_in(self, request, value, length):
        return self.dev.ctrl_transfer(0xA1, request, value, self.interface, length, timeout=5000)


def _backend():
    import usb.backend.libusb1
    try:
        import libusb_package
        return usb.backend.libusb1.get_backend(find_library=lambda _: libusb_package.get_library_path())
    except ImportError:
        return usb.backend.libusb1.get_backend()


def find():
    """True if a Neutron in bootloader mode is connected (and reachable through libusb)."""
    import usb.core
    try:
        return usb.core.find(idVendor=VID, idProduct=PID, backend=_backend()) is not None
    except usb.core.USBError:
        return False


def open_bootloader():
    """Open the ROM bootloader. Raises DfuError with a readable reason."""
    import usb.core
    import usb.util
    try:
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=_backend())
    except usb.core.NoBackendError as e:
        raise DfuError("libusb is not available") from e
    if dev is None:
        raise DfuError("no Neutron in bootloader mode found")
    try:
        cfg = dev.get_active_configuration()
        intf = next(i for i in cfg if i.bInterfaceClass == 0xFE and i.bInterfaceSubClass == 1
                    and i.bAlternateSetting == 0)
        name = usb.util.get_string(dev, intf.iInterface)
        transfer = DEFAULT_TRANSFER
        extra = bytes(intf.extra_descriptors or b"")
        if len(extra) >= 7 and extra[1] == 0x21:
            transfer = extra[5] | extra[6] << 8
        usb.util.claim_interface(dev, intf.bInterfaceNumber)
    except (usb.core.USBError, NotImplementedError, StopIteration) as e:
        raise DfuError(f"cannot open the bootloader ({e}). Its USB driver must be WinUSB.") from e
    return Bootloader(UsbTransport(dev, intf.bInterfaceNumber), name, transfer)
