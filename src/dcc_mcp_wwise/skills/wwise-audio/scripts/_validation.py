import re
from pathlib import Path

_NAME = re.compile(r"^[^\\/:*?\"<>|.%]+$")


def authoring_name(value, field):
    name = str(value).strip()
    if not name or len(name) > 128 or not _NAME.fullmatch(name):
        raise ValueError(f"{field} contains characters Wwise does not allow")
    return name


def wav_file(value):
    path = Path(str(value)).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".wav":
        raise ValueError("audio_file must be an existing WAV file")
    return str(path)


def target_path(value):
    path = str(value).strip()
    allowed = ("\\Actor-Mixer Hierarchy\\", "\\Interactive Music Hierarchy\\")
    if not path.startswith(allowed) or len(path) > 1024:
        raise ValueError("target must be an Actor-Mixer or Interactive Music project path")
    return path
