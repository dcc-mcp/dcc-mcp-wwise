from pathlib import Path

from dcc_mcp_core.skill import skill_entry, skill_success
from dcc_mcp_core.skills_helper import load_json_text

_MAX_PROJECT_INFO_BYTES = 8 * 1024 * 1024
_MAX_METADATA_FILE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_METADATA_BYTES = 128 * 1024 * 1024
_MAX_METADATA_FILES = 512
_MAX_PLATFORMS = 256
_MAX_BANK_RECORDS = 100_000


def _load_mapping(path, *, max_bytes):
    try:
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
    except OSError as exc:
        raise ValueError(f"could not read {path.name}: {exc}") from exc
    if len(data) > max_bytes:
        raise ValueError(f"{path.name} exceeds the {max_bytes}-byte metadata limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path.name} must contain UTF-8 JSON") from exc
    return load_json_text(text, source=path.name, require_mapping=True)


def _resolve_under(root, value, field):
    relative = str(value or "").strip()
    if not relative:
        raise ValueError(f"{field} must be a non-empty relative path")
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field} must stay inside generated_soundbanks_dir") from exc
    return candidate


def _metadata_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


@skill_entry
def main(generated_soundbanks_dir, max_items=500, **_kwargs):
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 5000:
        raise ValueError("max_items must be an integer between 1 and 5000")
    root = Path(str(generated_soundbanks_dir)).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("generated_soundbanks_dir must be an existing directory")

    project_file = _resolve_under(root, "ProjectInfo.json", "ProjectInfo.json")
    if not project_file.is_file():
        raise ValueError("ProjectInfo.json is missing from generated_soundbanks_dir")
    project_info = _load_mapping(project_file, max_bytes=_MAX_PROJECT_INFO_BYTES)
    info = project_info.get("ProjectInfo")
    if not isinstance(info, dict):
        raise ValueError("ProjectInfo.json does not contain Wwise project metadata")
    platforms = _metadata_list(info.get("Platforms"), "ProjectInfo.Platforms")
    if not platforms:
        raise ValueError("ProjectInfo.json does not contain Wwise platform metadata")
    if len(platforms) > _MAX_PLATFORMS:
        raise ValueError(f"ProjectInfo.json exceeds the {_MAX_PLATFORMS}-platform limit")

    banks = []
    missing = []
    bank_count = 0
    event_count = 0
    media_count = 0
    missing_count = 0
    metadata_file_count = 0
    metadata_bytes = 0

    def record_missing(path):
        nonlocal missing_count
        missing_count += 1
        if len(missing) < max_items:
            missing.append(str(path))

    for platform in platforms:
        if not isinstance(platform, dict):
            continue
        platform_path = _resolve_under(
            root,
            platform.get("Path") or platform.get("Name"),
            "ProjectInfo.Platforms[].Path",
        )
        if not platform_path.is_dir():
            record_missing(platform_path)
            continue
        metadata_files = sorted(platform_path.glob("*.json"))
        if len(metadata_files) > _MAX_METADATA_FILES:
            raise ValueError(
                f"{platform_path.name} exceeds the {_MAX_METADATA_FILES}-metadata-file limit"
            )
        for metadata_file in metadata_files:
            metadata_file = _resolve_under(
                platform_path,
                metadata_file.name,
                "SoundBank metadata path",
            )
            try:
                file_bytes = metadata_file.stat().st_size
            except OSError as exc:
                raise ValueError(f"could not inspect {metadata_file.name}: {exc}") from exc
            metadata_bytes += file_bytes
            if metadata_bytes > _MAX_TOTAL_METADATA_BYTES:
                raise ValueError("SoundBank metadata exceeds the total metadata byte limit")
            metadata_file_count += 1
            metadata = _load_mapping(metadata_file, max_bytes=_MAX_METADATA_FILE_BYTES)
            soundbanks_info = metadata.get("SoundBanksInfo")
            if not isinstance(soundbanks_info, dict):
                continue
            soundbanks = _metadata_list(
                soundbanks_info.get("SoundBanks"),
                f"{metadata_file.name}.SoundBanksInfo.SoundBanks",
            )
            for bank in soundbanks:
                if not isinstance(bank, dict):
                    continue
                bank_count += 1
                if bank_count > _MAX_BANK_RECORDS:
                    raise ValueError(
                        f"SoundBank metadata exceeds the {_MAX_BANK_RECORDS}-bank limit"
                    )
                bank_file = _resolve_under(
                    platform_path,
                    bank.get("Path"),
                    f"{metadata_file.name}.SoundBanks[].Path",
                )
                if not bank_file.is_file():
                    record_missing(bank_file)
                events = _metadata_list(
                    bank.get("Events"), f"{metadata_file.name}.SoundBanks[].Events"
                )
                media = _metadata_list(
                    bank.get("Media"), f"{metadata_file.name}.SoundBanks[].Media"
                )
                event_count += len(events)
                media_count += len(media)
                if len(banks) < max_items:
                    banks.append(
                        {
                            "platform": soundbanks_info.get("Platform") or platform.get("Name"),
                            "name": bank.get("ShortName"),
                            "path": str(bank_file),
                            "events": events,
                            "media": media,
                        }
                    )

    project = info.get("Project", {})
    return skill_success(
        "Inspected generated Wwise SoundBank delivery metadata.",
        project=project.get("Name") if isinstance(project, dict) else None,
        generated_soundbanks_dir=str(root),
        platform_count=len(platforms),
        metadata_file_count=metadata_file_count,
        metadata_bytes=metadata_bytes,
        bank_count=bank_count,
        event_count=event_count,
        media_count=media_count,
        missing_file_count=missing_count,
        missing_files=missing,
        missing_files_truncated=missing_count > len(missing),
        banks=banks,
        banks_truncated=bank_count > len(banks),
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
