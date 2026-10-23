"""Windows MIDI through winmm.dll (ctypes, no extra packages): find the Swing, send and receive SysEx.

A port object offers send(bytes), receive(timeout) -> one complete SysEx message or None,
drain() and close(). device.py only relies on those four methods.
"""
import ctypes
import queue
import threading
import time
from ctypes import wintypes

MIM_DATA = 0x3C3
MIM_LONGDATA = 0x3C4
CALLBACK_FUNCTION = 0x30000
MMSYSERR_NOERROR = 0
MIDIERR_STILLPLAYING = 65
SYSEX_START = 0xF0
SYSEX_END = 0xF7

BUFFER_SIZE = 1024
NUM_BUFFERS = 4
SEND_TIMEOUT = 2.0
# The official editor leaves about 100 ms between messages to the Swing.
QUIET_AFTER_RECEIVE = 0.1

DEFAULT_PORT_NAME = "SWING"


class MIDIHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                ("dwFlags", wintypes.DWORD), ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_size_t), ("dwOffset", wintypes.DWORD),
                ("dwReserved", ctypes.c_size_t * 8)]


class MIDIINCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                ("szPname", ctypes.c_wchar * 32), ("dwSupport", wintypes.DWORD)]


class MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                ("szPname", ctypes.c_wchar * 32), ("wTechnology", wintypes.WORD),
                ("wVoices", wintypes.WORD), ("wNotes", wintypes.WORD),
                ("wChannelMask", wintypes.WORD), ("dwSupport", wintypes.DWORD)]


MIDIINPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t,
                                ctypes.c_size_t, ctypes.c_size_t)


class MidiError(Exception):
    """A MIDI port could not be found, opened or used."""


def _winmm(winmm):
    return winmm if winmm is not None else ctypes.windll.winmm


def list_ports(winmm=None):
    """Return (input names, output names), indexed like winmm device ids."""
    w = _winmm(winmm)
    ins, outs = [], []
    for i in range(w.midiInGetNumDevs()):
        caps = MIDIINCAPSW()
        ins.append(caps.szPname if w.midiInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0 else None)
    for i in range(w.midiOutGetNumDevs()):
        caps = MIDIOUTCAPSW()
        outs.append(caps.szPname if w.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0 else None)
    return ins, outs


def find_port(names, needle=DEFAULT_PORT_NAME):
    """Index of the first name containing needle (case-insensitive), or None."""
    for i, name in enumerate(names):
        if name and needle.upper() in name.upper():
            return i
    return None


def open_swing(winmm=None, needle=DEFAULT_PORT_NAME):
    """Open the Swing's input and output ports, found by name."""
    ins, outs = list_ports(winmm)
    in_index, out_index = find_port(ins, needle), find_port(outs, needle)
    if in_index is None or out_index is None:
        raise MidiError(f"MIDI port '{needle}' not found")
    return WinMidiPort(in_index, out_index, winmm)


def open_by_name(in_name=None, out_name=None, winmm=None):
    """Open an input and/or an output whose names contain the given text (e.g. loopMIDI ports).

    Only the side(s) named are opened; the other is None."""
    ins, outs = list_ports(winmm)
    in_index = find_port(ins, in_name) if in_name else None
    out_index = find_port(outs, out_name) if out_name else None
    if in_name and in_index is None:
        raise MidiError(f"MIDI input '{in_name}' not found")
    if out_name and out_index is None:
        raise MidiError(f"MIDI output '{out_name}' not found")
    return WinMidiPort(in_index, out_index, winmm)


class WinMidiPort:
    """in_index or out_index may be None to open only one side."""

    def __init__(self, in_index, out_index, winmm=None):
        self._w = _winmm(winmm)
        self._hin = wintypes.HANDLE()
        self._hout = wintypes.HANDLE()
        self._in_open = self._out_open = False
        self._closing = False
        self._lock = threading.Lock()
        self._chunks = queue.Queue()     # (bytes, header address) filled by the winmm callback
        self._last_receive = None        # arrival time of the latest SysEx bytes from the device
        self._clock, self._sleep = time.monotonic, time.sleep
        self._pending = bytearray()      # bytes of a SysEx message not yet complete
        self._messages = []              # complete messages not yet returned by receive()
        self._buffers = {}               # header address -> (MIDIHDR, data buffer)
        self.on_short = None             # on_short(status, data1, data2) for notes/CCs, on a winmm thread
        self._callback = MIDIINPROC(self._on_midi_in)  # keep a reference while the port is open
        try:
            if in_index is not None:
                self._open_input(in_index)
            if out_index is not None:
                r = self._w.midiOutOpen(ctypes.byref(self._hout), out_index, 0, 0, 0)
                if r != MMSYSERR_NOERROR:
                    raise MidiError(f"midiOutOpen failed ({r}); is another program using the device?")
                self._out_open = True
        except BaseException:
            self.close()
            raise

    def _open_input(self, in_index):
        r = self._w.midiInOpen(ctypes.byref(self._hin), in_index, self._callback, 0, CALLBACK_FUNCTION)
        if r != MMSYSERR_NOERROR:
            raise MidiError(f"midiInOpen failed ({r}); is another program using the device?")
        self._in_open = True
        for _ in range(NUM_BUFFERS):
            data = ctypes.create_string_buffer(BUFFER_SIZE)
            hdr = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=BUFFER_SIZE)
            r = self._w.midiInPrepareHeader(self._hin, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if r != MMSYSERR_NOERROR:
                raise MidiError(f"midiInPrepareHeader failed ({r})")
            self._buffers[ctypes.addressof(hdr)] = (hdr, data)
            r = self._w.midiInAddBuffer(self._hin, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if r != MMSYSERR_NOERROR:
                raise MidiError(f"midiInAddBuffer failed ({r})")
        r = self._w.midiInStart(self._hin)
        if r != MMSYSERR_NOERROR:
            raise MidiError(f"midiInStart failed ({r})")

    def _on_midi_in(self, handle, msg, instance, param1, param2):
        # Runs on a winmm thread: only copy the bytes; buffers are given back in receive().
        if self._closing:
            return
        if msg == MIM_LONGDATA:
            hdr = ctypes.cast(param1, ctypes.POINTER(MIDIHDR)).contents
            self._last_receive = self._clock()
            self._chunks.put((ctypes.string_at(hdr.lpData, hdr.dwBytesRecorded), param1))
        elif msg == MIM_DATA and self.on_short is not None:
            self.on_short(param1 & 0xFF, (param1 >> 8) & 0x7F, (param1 >> 16) & 0x7F)

    def send(self, data):
        """Send one SysEx message and wait until the driver has finished with it."""
        data = bytes(data)
        if not self._out_open:
            raise MidiError("this port was opened without an output")
        if not data or data[0] != SYSEX_START or data[-1] != SYSEX_END:
            raise MidiError("only complete SysEx messages (F0 ... F7) can be sent")
        buf = ctypes.create_string_buffer(data, len(data))
        hdr = MIDIHDR(lpData=ctypes.cast(buf, ctypes.c_void_p), dwBufferLength=len(data))
        size = ctypes.sizeof(hdr)
        with self._lock:
            self._wait_quiet()
            r = self._w.midiOutPrepareHeader(self._hout, ctypes.byref(hdr), size)
            if r != MMSYSERR_NOERROR:
                raise MidiError(f"midiOutPrepareHeader failed ({r})")
            r = self._w.midiOutLongMsg(self._hout, ctypes.byref(hdr), size)
            deadline = time.monotonic() + SEND_TIMEOUT
            timed_out = False
            while self._w.midiOutUnprepareHeader(self._hout, ctypes.byref(hdr), size) == MIDIERR_STILLPLAYING:
                if not timed_out and time.monotonic() > deadline:
                    self._w.midiOutReset(self._hout)  # makes the driver hand the buffer back
                    timed_out = True
                time.sleep(0.005)
            if r != MMSYSERR_NOERROR:
                raise MidiError(f"midiOutLongMsg failed ({r})")
            if timed_out:
                raise MidiError("the MIDI driver did not finish sending in time")

    def send_short(self, status, data1=0, data2=0):
        """Send one short MIDI message (note, CC, or a one-byte real-time message such as clock)."""
        if not self._out_open:
            raise MidiError("this port was opened without an output")
        if not 0x80 <= status <= 0xFF or not 0 <= data1 <= 127 or not 0 <= data2 <= 127:
            raise MidiError(f"invalid short message {status:#x} {data1} {data2}")
        with self._lock:
            r = self._w.midiOutShortMsg(self._hout, status | (data1 << 8) | (data2 << 16))
        if r != MMSYSERR_NOERROR:
            raise MidiError(f"midiOutShortMsg failed ({r})")

    def _wait_quiet(self):
        """Leave the device QUIET_AFTER_RECEIVE seconds after the last bytes it sent."""
        last = self._last_receive
        if last is not None:
            wait = last + QUIET_AFTER_RECEIVE - self._clock()
            if wait > 0:
                self._sleep(wait)

    def receive(self, timeout):
        """Return the next complete SysEx message, or None if none arrives within timeout seconds."""
        deadline = time.monotonic() + timeout
        while not self._messages:
            remaining = deadline - time.monotonic()
            try:
                data, address = self._chunks.get(timeout=max(0.0, remaining))
            except queue.Empty:
                return None
            self._give_back(address)
            self._collect(data)
        return self._messages.pop(0)

    def drain(self):
        """Forget anything received so far."""
        while True:
            try:
                _, address = self._chunks.get_nowait()
            except queue.Empty:
                break
            self._give_back(address)
        self._pending.clear()
        self._messages.clear()

    def _give_back(self, address):
        if self._closing or address not in self._buffers:
            return
        hdr, _ = self._buffers[address]
        self._w.midiInAddBuffer(self._hin, ctypes.byref(hdr), ctypes.sizeof(hdr))

    def _collect(self, data):
        for byte in data:
            if byte == SYSEX_START:
                self._pending = bytearray([byte])
            elif self._pending:
                self._pending.append(byte)
                if byte == SYSEX_END:
                    self._messages.append(bytes(self._pending))
                    self._pending = bytearray()

    def close(self):
        self._closing = True
        if self._in_open:
            self._w.midiInStop(self._hin)
            self._w.midiInReset(self._hin)
            for hdr, _ in self._buffers.values():
                self._w.midiInUnprepareHeader(self._hin, ctypes.byref(hdr), ctypes.sizeof(hdr))
            self._w.midiInClose(self._hin)
            self._in_open = False
        if self._out_open:
            self._w.midiOutClose(self._hout)
            self._out_open = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
