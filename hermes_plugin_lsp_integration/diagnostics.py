"""Format LSP Diagnostic objects into readable markdown for context injection."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SEVERITY_MAP: dict[int, str] = {
    1: "Error",
    2: "Warning",
    3: "Info",
    4: "Hint",
}


SEVERITY_EMOJI: dict[str, str] = {
    "Error": "🔴",
    "Warning": "🟡",
    "Info": "🔵",
    "Hint": "⚪",
}


def format_diagnostics(diagnostics: list, file_path: str) -> str:
    """Return a formatted markdown string, or empty string if no diagnostics.

    Parameters
    ----------
    diagnostics : list
        A list of lsprotocol.types.Diagnostic objects (or dict equivalents).
    file_path : str
        The absolute path to the analysed file (used for the heading).
    """
    if not diagnostics:
        return ""

    filename = Path(file_path).name
    rows: list[str] = []
    severity_counts: dict[str, int] = {}

    for diag in diagnostics:
        severity_raw = _get_attr(diag, "severity", 1)
        severity_label = SEVERITY_MAP.get(severity_raw, "Unknown")
        severity_counts[severity_label] = severity_counts.get(severity_label, 0) + 1

        line = _get_attr(diag, "range", {})
        if isinstance(line, dict):
            line_num = line.get("start", {}).get("line", "?")
        else:
            # lsprotocol Range object
            try:
                line_num = line.start.line + 1  # 1-indexed for display
            except Exception:
                line_num = "?"

        message = _get_attr(diag, "message", "")
        # Truncate very long messages
        if len(message) > 200:
            message = message[:197] + "..."

        # Escape pipe characters in message for markdown table
        message = message.replace("|", "\\|")

        rows.append(f"| {severity_label} | {line_num} | {message} |")

    if not rows:
        return ""

    # Build summary
    parts: list[str] = []
    for sev in ("Error", "Warning", "Info", "Hint"):
        count = severity_counts.get(sev, 0)
        if count:
            parts.append(f"{count} {sev.lower()}(s)")

    summary = ", ".join(parts) if parts else "0 diagnostic(s)"

    lines = [
        f"## LSP Diagnostics: {filename}",
        "",
        "| Severity | Line | Message |",
        "|----------|------|---------|",
        *rows,
        "",
        summary,
    ]
    return "\n".join(lines)


def _get_attr(obj, attr: str, default=None):
    """Safely get an attribute from an object or dict."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)
