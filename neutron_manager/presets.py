"""Save and open the global settings as JSON files."""
import json

from . import __version__
from .protocol import ProtocolError, Settings

FORMAT = "neutron-manager-settings"


def save(path, settings):
    data = {"format": FORMAT, "version": __version__, "settings": settings.to_dict()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load(path):
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ProtocolError(f"not a settings file ({e})") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("settings"), dict):
        raise ProtocolError("not a Neutron Manager settings file")
    return Settings.from_dict(data["settings"])
