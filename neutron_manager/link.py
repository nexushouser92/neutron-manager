"""Keeps the Neutron linked while the window is open (same design as Swing Editor's link).

- Looks for the Neutron every POLL_INTERVAL seconds and opens it as soon as it appears.
- On linking, reads the firmware version; with a supported version it reads all settings.
- push(settings) writes WRITE_DELAY seconds after the latest change, only what differs, and checks it
  by reading everything back.
- Between jobs it asks for the settings every SETTINGS_POLL seconds without waiting for the reply.
  Each settings request makes the Neutron send a burst of 50 tuning readings over about 2 s, which
  become ("tuning", Tuning) events, and the reply shows changes made on the Neutron's panel (the
  Neutron does not announce them). A setting change message (opcode 90) also makes it read again.

All device work happens in step(), called from one worker thread (or directly by tests).
Events, a queue of (kind, payload):
  ("searching", error or None) ("linked", None) ("info", (comms, version)) ("outdated", version)
  ("settings", Settings) ("writing", None) ("written", Settings) ("write_failed", error) ("tuning", Tuning)
"""
import queue
import threading
import time

from . import device, midi_win, protocol

POLL_INTERVAL = 1.0
WRITE_DELAY = 0.3
LISTEN_SLICE = 0.03
SETTINGS_POLL = 1.0       # the factory editor polls at the same rate
POLL_REPLY_WAIT = 0.5     # a write waits at most this long for an outstanding poll reply


class NeutronLink:
    def __init__(self, opener=midi_win.open_neutron, lister=midi_win.list_ports, clock=time.monotonic,
                 write_delay=WRITE_DELAY):
        self.opener, self.lister, self.clock = opener, lister, clock
        self.write_delay = write_delay
        self.events = queue.Queue()
        self.port = None
        self.on_neutron = None       # settings the Neutron has (last read or written)
        self.version = None
        self._lock = threading.Lock()
        self._pending = None
        self._due = 0.0
        self._want_read = False
        self._actions = []           # "restore_defaults", "reset_key_range", "reset_lfo_order"
        self._next_poll = 0.0
        self._next_settings_poll = 0.0
        self._poll_sent = None       # time of a settings request whose reply has not arrived yet
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------- called by the window
    @property
    def linked(self):
        return self.port is not None

    @property
    def editable(self):
        return self.linked and self.version is not None and protocol.version_supported(self.version)

    @property
    def has_pending(self):
        return self._pending is not None

    def push(self, settings):
        with self._lock:
            self._pending = settings
            self._due = self.clock() + self.write_delay

    def flush(self):
        with self._lock:
            self._due = 0.0

    def request_read(self):
        self._want_read = True

    def request_action(self, name):
        with self._lock:
            self._actions.append(name)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout=3.0):
        self.flush()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        if self.editable and self._pending is not None:
            self.step()
        self._close()

    # ------------------------------------------------------------- worker
    def _run(self):
        while not self._stop.is_set():
            self.step()

    def step(self):
        now = self.clock()
        if self.port is None:
            if now >= self._next_poll:
                self._next_poll = now + POLL_INTERVAL
                self._try_link()
            else:
                self._stop.wait(0.05)
            return
        if now >= self._next_poll:
            self._next_poll = now + POLL_INTERVAL
            if not self._present():
                self._drop(None)
                return
        if not self.editable:
            self._listen()
            return
        if self._poll_sent is not None:
            # A poll reply is on its way: any request now could take it for its own reply.
            if now - self._poll_sent <= POLL_REPLY_WAIT:
                self._listen()
                return
            self._poll_sent = None   # that reply was lost; carry on
        with self._lock:
            action = self._actions.pop(0) if self._actions else None
        if action is not None:
            self._run_action(action)
            return
        if self._want_read:
            self._want_read = False
            self._read()
            return
        with self._lock:
            settings = self._pending if self._pending is not None and now >= self._due else None
            idle = self._pending is None
        if settings is not None:
            self._write(settings)
            return
        if idle and now >= self._next_settings_poll:
            self._send_poll(now)
            return
        self._listen()

    def _send_poll(self, now):
        self._next_settings_poll = now + SETTINGS_POLL
        try:
            self.port.send(protocol.settings_request())
        except (midi_win.MidiError, OSError) as e:
            self._drop(e)
            return
        self._poll_sent = now

    def _on_poll_reply(self, payload):
        self._poll_sent = None
        try:
            settings = protocol.parse_settings(payload)
        except protocol.ProtocolError:
            return
        if settings != self.on_neutron and not self.has_pending:
            self.on_neutron = settings
            self.events.put(("settings", settings))

    def _on_other(self, opcode, payload):
        """Messages the Neutron sends on its own."""
        if opcode == protocol.TUNING_REPLY:
            try:
                self.events.put(("tuning", protocol.parse_tuning(payload)))
            except protocol.ProtocolError:
                pass
        elif opcode == protocol.PARAM_CHANGED:
            self._want_read = True   # read everything again: values come in panel units that way
        elif opcode == protocol.SETTINGS_REPLY and self._poll_sent is not None:
            self._on_poll_reply(payload)

    def _listen(self):
        try:
            msg = self.port.receive(LISTEN_SLICE)
        except (midi_win.MidiError, OSError) as e:
            self._drop(e)
            return
        parsed = protocol.parse(msg) if msg else None
        if parsed is not None:
            self._on_other(parsed[0], parsed[2])

    def _present(self):
        try:
            ins, outs = self.lister()
        except OSError:
            return False
        return midi_win.find_port(ins) is not None and midi_win.find_port(outs) is not None

    def _try_link(self):
        if not self._present():
            return
        try:
            port = self.opener()
        except (midi_win.MidiError, OSError) as e:
            self.events.put(("searching", e))
            return
        self.port = port
        self.events.put(("linked", None))
        try:
            comms, self.version = device.read_version(port, self._on_other)
        except (device.DeviceError, protocol.ProtocolError, midi_win.MidiError, OSError) as e:
            self._drop(e)
            return
        self.events.put(("info", (comms, self.version)))
        if not protocol.version_supported(self.version):
            self.events.put(("outdated", self.version))
            return
        if self._read() and self._pending is not None:
            self.flush()

    def _read(self):
        try:
            self.on_neutron = device.read_settings(self.port, self._on_other)
        except (device.DeviceError, protocol.ProtocolError, midi_win.MidiError, OSError) as e:
            self._drop(e)
            return False
        self.events.put(("settings", self.on_neutron))
        return True

    def _write(self, settings):
        if not settings.differences(self.on_neutron):
            self._clear_pending(settings)
            return
        self.events.put(("writing", None))
        try:
            self.on_neutron = device.write_and_verify(self.port, settings, self.on_neutron, self._on_other)
        except device.VerifyError as e:
            self._want_read = True
            self._clear_pending(settings)
            self.events.put(("write_failed", e))
            return
        except (device.DeviceError, protocol.ProtocolError, midi_win.MidiError, OSError) as e:
            self.events.put(("write_failed", e))
            self._drop(e)
            return
        self._clear_pending(settings)
        self.events.put(("written", self.on_neutron))

    def _run_action(self, action):
        self.events.put(("writing", None))
        try:
            if action == "restore_defaults":
                self.on_neutron = device.restore_defaults(self.port, self._on_other)
            elif action == "reset_key_range":
                self.on_neutron = device.reset_action(self.port, protocol.RESET_KEY_RANGE_ID, self._on_other)
            elif action == "reset_lfo_order":
                self.on_neutron = device.reset_action(self.port, protocol.RESET_LFO_ORDER_ID, self._on_other)
            else:
                return
        except (device.DeviceError, protocol.ProtocolError, midi_win.MidiError, OSError) as e:
            self.events.put(("write_failed", e))
            self._drop(e)
            return
        with self._lock:
            self._pending = None
        self.events.put(("written", self.on_neutron))

    def _clear_pending(self, written):
        with self._lock:
            if self._pending is written:
                self._pending = None

    def _drop(self, error):
        self._close()
        self.version = None
        self._next_poll = self.clock() + POLL_INTERVAL
        self.events.put(("searching", error))

    def _close(self):
        port, self.port = self.port, None
        if port is not None:
            try:
                port.close()
            except (midi_win.MidiError, OSError):
                pass
