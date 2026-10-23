"""Stand-ins for a Neutron on a MIDI port and for the STM32 ROM bootloader, built from SPEC.md and the
messages captured from a real Neutron (research/probe_read.log)."""
import queue
import struct

from neutron_manager import dfu, firmware, protocol

# Captured from a Neutron with firmware 2.0.2 (research/probe_read.log)
VERSION_REPLY = bytes.fromhex("F0002032280074" "01" "322E302E32" "F7")
SETTINGS_REPLY = bytes.fromhex("F0002032280006" "01" "0902000002310858" "4600000000000000"
                               "7F2F000000000001" "F7")
TUNING_REPLY = bytes.fromhex("F0002032280072" "01" "0E09000000000001" "5815000000000101" "F7")
CAPTURED_WORDS = [0x0209, 0x8000, 0x3182, 0x4688, 0, 0, 0, 0x2FFF, 0, 0]

# What the Neutron stores for each panel value (the inverse of the transforms in the spec)
ENCODE = {
    "osc_key_split": lambda v: 0 if v == 0 else v - 23,
    "key_range_min": lambda v: v - 24,
    "key_range_max": lambda v: 96 - v,
}


class FakeNeutron:
    """Behaves like a Neutron's MIDI port: answers requests and applies parameter writes."""

    def __init__(self, words=None, version=b"2.0.2", stream_tuning=0, ignore=()):
        self.words = list(words or CAPTURED_WORDS)
        self.version = version
        self.stream_tuning = stream_tuning     # tuning readings sent after each settings request
                                               # (a real Neutron sends a burst of 50 over ~2 s)
        self.ignore = set(ignore)              # parameter numbers the fake does not store (to test verify)
        self.inbox = queue.Queue()
        self.sent = []
        self.closed = False
        self.on_short = None

    def _reply(self, opcode, payload, header=1):
        self.inbox.put(bytes([0xF0, 0, 0x20, 0x32, 0x28, 0, opcode, header, *payload, 0xF7]))
        if opcode == protocol.SETTINGS_REPLY:
            for _ in range(self.stream_tuning):
                self.inbox.put(TUNING_REPLY)

    def send(self, data):
        data = bytes(data)
        self.sent.append(data)
        opcode = data[6]
        if opcode == protocol.VERSION_REQUEST:
            self._reply(protocol.VERSION_REPLY, self.version)
        elif opcode == protocol.SETTINGS_REQUEST:
            raw = b"".join(struct.pack("<H", w) for w in self.words)
            self._reply(protocol.SETTINGS_REPLY, protocol.pack7(raw))
        elif opcode == protocol.PARAM_SET:
            self._set(data[7], data[8:-1])
        elif opcode == protocol.RESTORE_DEFAULTS:
            self.words = list(CAPTURED_WORDS)

    def _store(self, param, raw):
        mask = ((1 << param.width) - 1) << param.offset
        self.words[param.block] = (self.words[param.block] & ~mask) | ((raw << param.offset) & mask)

    def _set(self, pid, data):
        if pid in self.ignore:
            return
        if pid == protocol.RESET_KEY_RANGE_ID:
            self._store(protocol.BY_KEY["key_range_min"], 0)
            self._store(protocol.BY_KEY["key_range_max"], 0)
            return
        if pid == protocol.RESET_LFO_ORDER_ID:
            self._store(protocol.BY_KEY["lfo_shape_order"], 0x4688)
            return
        paraphonic = bool((self.words[0] >> 7) & 1)
        param = protocol.param_for_pid(pid, paraphonic)
        if param.kind == "array":
            bits = param.width // param.elements
            index, value = data
            mask = ((1 << bits) - 1) << (param.offset + bits * index)
            self.words[param.block] = (self.words[param.block] & ~mask) | (value << (param.offset + bits * index))
        else:
            self._store(param, ENCODE.get(param.key, lambda v: v)(data[0]))

    def receive(self, timeout):
        try:
            return self.inbox.get_nowait()
        except queue.Empty:
            return None

    def drain(self):
        while not self.inbox.empty():
            self.inbox.get_nowait()

    def close(self):
        self.closed = True


class FakeBootloader:
    """The STM32 ROM bootloader's DFU state machine over a 128 KB flash (a transport for dfu.Bootloader)."""

    LAYOUT = "@Internal Flash  /0x08000000/64*002Kg"

    def __init__(self, flash=None, transfer=2048, readable=True):
        if flash is None:   # a plausible factory image: valid vector table, then varied bytes
            flash = bytearray(bytes(range(256)) * 512)
            struct.pack_into("<II", flash, 0, 0x20004000, firmware.FLASH_BASE + 0xC1)
        self.flash = bytearray(flash)
        self.transfer = transfer
        self.readable = readable
        self.state = dfu.STATE_IDLE
        self.pointer = firmware.FLASH_BASE
        self.pending = None
        self.erased = []
        self.writes = []
        self.mass_erased = False
        self.left = False

    def ctrl_out(self, request, value, data):
        data = bytes(data)
        if request == dfu.DNLOAD:
            if self.state not in (dfu.STATE_IDLE, dfu.STATE_DNLOAD_IDLE):
                self.state = dfu.STATE_ERROR
                return
            if not data:
                self.left = True
                self.state = dfu.STATE_MANIFEST
                return
            self.pending = (value, data)
            self.state = dfu.STATE_DNLOAD_SYNC
        elif request in (dfu.ABORT, dfu.CLRSTATUS):
            self.state = dfu.STATE_IDLE

    def ctrl_in(self, request, value, length):
        if request == dfu.GETSTATUS:
            if self.state == dfu.STATE_DNLOAD_SYNC:
                self._execute()
            return bytes([0, 1, 0, 0, self.state, 0])
        if request == dfu.UPLOAD:
            if self.state not in (dfu.STATE_IDLE, dfu.STATE_UPLOAD_IDLE):
                self.state = dfu.STATE_ERROR
                return b""
            self.state = dfu.STATE_UPLOAD_IDLE
            start = self.pointer - firmware.FLASH_BASE + (value - 2) * self.transfer
            if not self.readable:
                return bytes(length)
            return bytes(self.flash[start:start + length])
        return b""

    def _execute(self):
        block, data = self.pending
        self.state = dfu.STATE_DNLOAD_IDLE
        if block == 0:
            if data[0] == dfu.CMD_SET_ADDRESS:
                self.pointer = struct.unpack("<I", data[1:5])[0]
            elif data[0] == dfu.CMD_ERASE and len(data) == 5:
                address = struct.unpack("<I", data[1:5])[0]
                start = address - firmware.FLASH_BASE
                self.flash[start:start + firmware.PAGE_SIZE] = b"\xFF" * firmware.PAGE_SIZE
                self.erased.append(address)
            elif data[0] == dfu.CMD_ERASE:
                self.mass_erased = True
            return
        start = self.pointer - firmware.FLASH_BASE + (block - 2) * self.transfer
        page = start // firmware.PAGE_SIZE
        if any(b != 0xFF for b in self.flash[start:start + len(data)]):
            self.state = dfu.STATE_ERROR       # writing over flash that was not erased
            return
        self.flash[start:start + len(data)] = data
        self.writes.append(page)
