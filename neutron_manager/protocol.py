"""Neutron SysEx protocol (factory firmware 2.x), written from SPEC.md.

Every message is F0 00 20 32 28 <device> <opcode> <data...> F7. The settings reply carries ten 16-bit
little-endian words packed in 7-bit groups; each setting is a bit field in one of those words.
Settings are kept in "panel units": what the window shows (a note number, a semitone index, a choice).
"""
from dataclasses import dataclass, field

SOX, EOX = 0xF0, 0xF7
MANUFACTURER = (0x00, 0x20, 0x32)
FAMILY = 0x28
ALL_DEVICES = 0x7F
HEADER = bytes([SOX, *MANUFACTURER, FAMILY, ALL_DEVICES])

SETTINGS_REQUEST = 5
SETTINGS_REPLY = 6
PARAM_SET = 10
RESTORE_DEFAULTS = 11
PARAM_CHANGED = 90       # sent by the Neutron when a setting changes on its panel
TUNING_REPLY = 114       # sent by the Neutron continuously: both oscillators' measured period
VERSION_REQUEST = 115
VERSION_REPLY = 116

RESET_KEY_RANGE_ID = 6
RESET_LFO_ORDER_ID = 57
WORDS = 10
MIN_VERSION = (2, 0, 2)

SECTIONS = ("oscillators", "lfo", "filter", "midi", "keyboard")


class ProtocolError(Exception):
    """A message or a value that does not follow the protocol."""


# ------------------------------------------------------------------ 7-bit packing
def pack7(data):
    """8-bit bytes -> 7-bit SysEx bytes: groups of 7, each followed by a byte holding their top bits.

    The input is padded with zeros to a multiple of 7; when it already is one, 7 zeros are added."""
    data = bytes(data) + bytes(7 - len(data) % 7)
    out = bytearray()
    for start in range(0, len(data), 7):
        group = data[start:start + 7]
        out += bytes(b & 0x7F for b in group)
        out.append(sum(((b >> 7) & 1) << i for i, b in enumerate(group)))
    return bytes(out)


def unpack7(data):
    """Inverse of pack7: groups of 8 bytes (7 data bytes + their top bits) -> 7 bytes each."""
    data = bytes(data)
    if any(b > 0x7F for b in data):
        raise ProtocolError("packed data has a byte above 0x7F")
    if len(data) % 8:
        raise ProtocolError(f"packed data must come in groups of 8 bytes, got {len(data)}")
    out = bytearray()
    for start in range(0, len(data), 8):
        group, high = data[start:start + 7], data[start + 7]
        out += bytes(b | (((high >> i) & 1) << 7) for i, b in enumerate(group))
    return bytes(out)


# ------------------------------------------------------------------ messages
def message(opcode, *data):
    for b in data:
        if not 0 <= b <= 0x7F:
            raise ProtocolError(f"data byte {b} is not 7-bit")
    return HEADER + bytes([opcode, *data, EOX])


def settings_request():
    return message(SETTINGS_REQUEST)


def version_request():
    return message(VERSION_REQUEST)


def restore_defaults_request():
    return message(RESTORE_DEFAULTS)


def parse(msg):
    """(opcode, header byte, payload) of a Neutron message, or None for anything else."""
    msg = bytes(msg)
    if len(msg) < 8 or msg[0] != SOX or msg[-1] != EOX or tuple(msg[1:4]) != MANUFACTURER or msg[4] != FAMILY:
        return None
    return msg[6], msg[7], msg[8:-1]


def parse_version(payload):
    """'2.0.2' -> (2, 0, 2). Only digits and dots count; '2.02' reads as 2.0.2."""
    text = "".join(chr(b) for b in payload if chr(b).isdigit() or chr(b) == ".")
    parts = [p for p in text.split(".") if p]
    if len(parts) == 2 and len(parts[1]) == 2:
        parts = [parts[0], parts[1][0], parts[1][1]]
    if len(parts) < 3:
        raise ProtocolError(f"unreadable firmware version {bytes(payload)!r}")
    return tuple(int(p) for p in parts[:3])


def version_supported(version):
    """The factory editor protocol needs firmware 2.0.2 or later (major 0 = development builds)."""
    return version[0] == 0 or tuple(version) >= MIN_VERSION


@dataclass(frozen=True)
class Tuning:
    """One reading of the live tuner: both oscillators' period in microseconds and a note number."""
    note: int
    osc1_us: int
    osc2_us: int

    @staticmethod
    def frequency(period_us):
        return 1e6 / period_us if period_us else 0.0

    @property
    def osc1_hz(self):
        return self.frequency(self.osc1_us)

    @property
    def osc2_hz(self):
        return self.frequency(self.osc2_us)


def parse_tuning(payload):
    data = unpack7(payload)
    if len(data) < 14:
        raise ProtocolError("tuning reply too short")
    periods = [int.from_bytes(data[i:i + 4], "little") for i in (0, 7)]
    return Tuning(note=data[4], osc1_us=periods[0], osc2_us=periods[1])


# ------------------------------------------------------------------ settings
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(note):
    """MIDI note -> name with middle C (60) as C3, the convention used on Behringer synths."""
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 2}"


@dataclass(frozen=True)
class Param:
    key: str
    pid: int                 # parameter number used when writing
    block: int               # which 16-bit word of the settings reply
    offset: int
    width: int
    section: str
    kind: str                # "bool", "choice", "number", "note", "array"
    values: tuple = ()       # allowed panel values, in display order
    labels: tuple = ()       # option label keys for "choice" (translated by i18n)
    elements: int = 0        # for "array": how many elements share the field
    default: object = 0

    def decode(self, word):
        raw = (word >> self.offset) & ((1 << self.width) - 1)
        if self.kind == "bool":
            return raw == 1
        if self.kind == "array":
            bits = self.width // self.elements
            return tuple((raw >> (bits * i)) & ((1 << bits) - 1) for i in range(self.elements))
        return DECODERS.get(self.key, lambda r: r)(raw)

    def check(self, value):
        """Raise ProtocolError unless value is allowed for this setting."""
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ProtocolError(f"{self.key} must be on or off")
        elif self.kind == "array":
            limit = 1 << (self.width // self.elements)
            if len(value) != self.elements or any(not 0 <= v < limit for v in value):
                raise ProtocolError(f"{self.key} needs {self.elements} values below {limit}")
        elif value not in self.values:
            raise ProtocolError(f"{value} is not a valid value for {self.key}")

    def write_messages(self, value, old=None):
        """SysEx messages that set this parameter to value (only changed elements for arrays)."""
        self.check(value)
        if self.kind == "bool":
            return [message(PARAM_SET, self.pid, int(value))]
        if self.kind == "array":
            return [message(PARAM_SET, self.pid, i, v) for i, v in enumerate(value)
                    if old is None or old[i] != v]
        return [message(PARAM_SET, self.pid, value & 0x7F)]


DECODERS = {
    "osc_key_split": lambda r: 0 if r == 0 else r + 23,
    "key_range_min": lambda r: r + 24,
    "key_range_max": lambda r: 96 - r,
}


def _choice(key, pid, block, offset, width, section, labels, default=0):
    return Param(key, pid, block, offset, width, section, "choice", tuple(range(len(labels))), tuple(labels),
                 default=default)


def _bool(key, pid, block, offset, section, default=False):
    return Param(key, pid, block, offset, 1, section, "bool", (False, True), default=default)


def _number(key, pid, block, offset, width, section, low, high, default=0):
    return Param(key, pid, block, offset, width, section, "number", tuple(range(low, high + 1)), default=default)


def _note(key, pid, block, offset, width, section, notes, default):
    return Param(key, pid, block, offset, width, section, "note", tuple(notes), default=default)


RANGES = ("range_32", "range_16", "range_8", "range_wide")

PARAMS = (
    # oscillators
    _choice("osc1_range", 38, 0, 0, 2, "oscillators", RANGES),
    _choice("osc2_range", 39, 0, 2, 2, "oscillators", RANGES),
    _bool("osc1_blend_switch", 32, 0, 13, "oscillators"),
    _bool("osc2_blend_switch", 33, 0, 14, "oscillators"),
    _bool("osc1_tune_bypass", 34, 1, 1, "oscillators"),
    _bool("osc2_tune_bypass", 35, 1, 2, "oscillators"),
    _number("osc1_autoglide", 36, 2, 5, 5, "oscillators", 0, 24, default=12),
    _number("osc2_autoglide", 37, 2, 10, 5, "oscillators", 0, 24, default=12),
    _bool("osc_sync", 14, 0, 4, "oscillators"),
    _bool("paraphonic", 15, 0, 7, "oscillators"),
    _note("osc_key_split", 40, 4, 10, 6, "oscillators", [0, *range(24, 87)], 0),
    _bool("osc2_key_hold", 42, 2, 15, "oscillators"),
    # lfo
    _bool("lfo_blend_switch", 48, 0, 15, "lfo"),
    _bool("lfo_key_sync", 55, 0, 5, "lfo"),
    _bool("lfo_one_shot", 49, 6, 14, "lfo"),
    _bool("lfo_retrigger", 59, 6, 15, "lfo"),
    _bool("lfo_midi_sync", 53, 3, 15, "lfo"),
    _note("lfo_key_track", 50, 4, 0, 7, "lfo", [0, *range(12, 109)], 0),
    _number("lfo_depth", 52, 7, 6, 6, "lfo", 0, 63, default=63),
    Param("lfo_shape_order", 56, 3, 0, 15, "lfo", "array", elements=5, default=(0, 1, 2, 3, 4)),
    Param("lfo_phase_offset", 58, 5, 0, 15, "lfo", "array", elements=5, default=(0, 0, 0, 0, 0)),
    # filter
    _choice("vcf_mode", 16, 0, 8, 2, "filter", ("vcf_hp", "vcf_bp", "vcf_lp"), default=2),
    _bool("vcf_key_track", 17, 0, 6, "filter"),
    _choice("vcf_mod_source", 18, 6, 12, 2, "filter", ("off", "aftertouch", "mod_wheel", "velocity")),
    _number("vcf_mod_depth", 20, 7, 0, 6, "filter", 0, 63, default=63),
    # midi
    _number("midi_channel", 0, 1, 9, 4, "midi", 0, 15),
    _bool("disable_midi_dips", 10, 1, 13, "midi"),
    _bool("polychain", 8, 1, 3, "midi"),
    _number("pitch_bend_range", 3, 2, 0, 5, "midi", 0, 24, default=2),
    _choice("assign_out", 4, 0, 10, 3, "midi", ("osc1_cv", "osc2_cv", "velocity", "mod_wheel", "aftertouch")),
    _bool("env_legato", 5, 1, 0, "midi"),
    _choice("note_priority", 1, 1, 14, 2, "midi", ("prio_low", "prio_high", "prio_last"), default=2),
    _choice("para_note_priority", 1, 7, 12, 2, "midi", ("prio_low", "prio_high", "prio_last", "prio_hilo"),
            default=2),
    # keyboard
    _bool("key_range_mute", 11, 5, 15, "keyboard"),
    _note("key_range_min", 12, 6, 0, 6, "keyboard", range(24, 88), 24),
    _note("key_range_max", 13, 6, 6, 6, "keyboard", range(33, 97), 96),
)
BY_KEY = {p.key: p for p in PARAMS}
BY_PID = {}
for _p in PARAMS:
    BY_PID.setdefault(_p.pid, []).append(_p)


def param_for_pid(pid, paraphonic):
    """The setting a PARAM_CHANGED message refers to; number 1 is the paraphonic priority in that mode."""
    params = BY_PID.get(pid)
    if not params:
        return None
    if len(params) > 1:
        return BY_KEY["para_note_priority" if paraphonic else "note_priority"]
    return params[0]


@dataclass(frozen=True)
class Settings:
    """All global settings, in panel units. Immutable: set() returns a new object."""
    values: dict = field(default_factory=lambda: {p.key: p.default for p in PARAMS})

    def __getitem__(self, key):
        return self.values[key]

    def __eq__(self, other):
        return isinstance(other, Settings) and self.values == other.values

    def __hash__(self):
        return hash(tuple(sorted(self.values.items())))

    def set(self, key, value):
        param = BY_KEY.get(key)
        if param is None:
            raise ProtocolError(f"unknown setting {key}")
        if param.kind == "array":
            value = tuple(value)
        param.check(value)
        return Settings({**self.values, key: value})

    def differences(self, other):
        """Keys whose value differs from other (in PARAMS order)."""
        return [p.key for p in PARAMS if other is None or self[p.key] != other[p.key]]

    def with_values_from(self, other, keys=None):
        result = self
        for key in keys or [p.key for p in PARAMS]:
            result = result.set(key, other[key])
        return result

    def to_dict(self):
        return {k: list(v) if isinstance(v, tuple) else v for k, v in self.values.items()}

    @classmethod
    def from_dict(cls, data):
        settings = cls()
        for key, value in data.items():
            if key in BY_KEY:
                settings = settings.set(key, value)
        return settings


def parse_settings(payload):
    """Settings reply payload (packed) -> Settings."""
    data = unpack7(payload)
    if len(data) < WORDS * 2:
        raise ProtocolError(f"settings reply has {len(data)} bytes, needs {WORDS * 2}")
    words = [int.from_bytes(data[i * 2:i * 2 + 2], "little") for i in range(WORDS)]
    return Settings({p.key: p.decode(words[p.block]) for p in PARAMS})
