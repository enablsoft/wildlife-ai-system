from __future__ import annotations

"""
Frame export and species-label utility helpers.

Function index:
- last_taxon_segment: extract lowest-rank taxonomy segment.
- species_string_is_blank: detect blank species labels.
- record_is_blank: detect blank rows from species and description.
- format_species_display: format species label for UI text.
- short_species_label: build compact species label for tables.
- export_frames_xlsx: build Excel payload for frame results.
"""

from datetime import datetime
from io import BytesIO


def last_taxon_segment(species: str) -> str:
    """Return the last non-empty semicolon-delimited taxonomy segment."""
    for seg in reversed([p.strip() for p in (species or "").split(";")]):
        if seg:
            return seg
    return ""


def species_string_is_blank(species: str) -> bool:
    """Return True when species taxonomy indicates a blank/no-match record."""
    sp = (species or "").lower()
    if "__blank" in sp:
        return True
    return last_taxon_segment(species or "").lower() == "blank"


def record_is_blank(species: str, description: str) -> bool:
    """Return True when species or description marks a row as blank."""
    desc = (description or "").lower()
    if "__blank" in desc:
        return True
    return species_string_is_blank(species or "")


def format_species_display(species: str, description: str) -> str:
    """Format species text for UI display."""
    if record_is_blank(species, description):
        return "No species match (blank)"
    return short_species_label(species, description)


def short_species_label(species: str, description: str) -> str:
    """Build a compact species label for tables and exports."""
    if record_is_blank(species, description):
        return "Blank"
    seg = last_taxon_segment(species or "")
    if seg:
        return seg
    cleaned = (species or "").replace("_", " ").strip().title()
    return cleaned if cleaned and cleaned != "Unknown" else "Unknown"


def export_frames_xlsx(
    records: list[dict[str, str]],
    hide_blanks: bool,
    log_file: str,
) -> bytes:
    """Build an XLSX workbook for frame results and return bytes."""
    try:
        from openpyxl import Workbook  # type: ignore[reportMissingModuleSource]
    except Exception as e:  # pragma: no cover
        raise RuntimeError("openpyxl is required for Excel export.") from e
    wb = Workbook()
    ws = wb.active
    ws.title = "frame_results"
    ws.append(
        [
            "job_id",
            "video_source",
            "frame",
            "species_label_short",
            "default_species_short",
            "default_species_type",
            "species_raw",
            "manual_tag",
            "description",
            "annotated_rel",
        ]
    )
    for r in records:
        ws.append(
            [
                r.get("job_id", ""),
                r.get("source", ""),
                r.get("frame", ""),
                short_species_label(r.get("species", ""), r.get("description", "")),
                r.get("species_short", ""),
                r.get("species_type", ""),
                r.get("species", ""),
                r.get("manual_tag", ""),
                r.get("description", ""),
                r.get("annotated_rel", ""),
            ]
        )
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J{max(2, len(records) + 1)}"
    meta = wb.create_sheet("meta")
    meta.append(["generated_at_utc", datetime.utcnow().isoformat()])
    meta.append(["hide_blanks", "1" if hide_blanks else "0"])
    meta.append(["rows", str(len(records))])
    meta.append(["log_file", log_file])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()

