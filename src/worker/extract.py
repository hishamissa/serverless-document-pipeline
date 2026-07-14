"""Deterministic extraction logic.

Pure functions with no AWS dependencies so they can be unit-tested directly.
Given raw document bytes/text, produce a JSON-serialisable summary.

Two document kinds are supported:
  * ``csv``  -> row count, column names, and per-numeric-column stats
  * ``text`` -> line/word/char counts and top keyword frequencies
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from typing import Any

# Words ignored when computing keyword frequencies for text documents.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of", "to",
    "in", "on", "for", "with", "as", "by", "at", "from", "is", "are", "was",
    "were", "be", "been", "being", "it", "its", "this", "that", "these",
    "those", "i", "you", "he", "she", "we", "they", "not", "no", "so",
}

_WORD_RE = re.compile(r"[a-z0-9']+")


class ExtractionError(ValueError):
    """Raised when a document cannot be parsed. Callers mark the job FAILED."""


def extract(content: str, doc_type: str) -> dict[str, Any]:
    """Dispatch to the correct extractor based on ``doc_type``."""
    doc_type = (doc_type or "").lower().strip()
    if doc_type == "csv":
        return extract_csv(content)
    if doc_type in ("text", "txt", "plain"):
        return extract_text(content)
    raise ExtractionError(f"unsupported document type: {doc_type!r}")


def extract_csv(content: str) -> dict[str, Any]:
    """Summarise a CSV: row count, columns, and numeric column stats."""
    if not content.strip():
        raise ExtractionError("csv document is empty")

    reader = csv.reader(io.StringIO(content))
    try:
        rows = list(reader)
    except csv.Error as exc:  # malformed quoting etc.
        raise ExtractionError(f"could not parse csv: {exc}") from exc

    if not rows:
        raise ExtractionError("csv document has no rows")

    header, *data_rows = rows
    columns = [c.strip() for c in header]
    if not any(columns):
        raise ExtractionError("csv header row is empty")

    numeric_stats: dict[str, dict[str, float]] = {}
    for idx, col in enumerate(columns):
        values: list[float] = []
        for row in data_rows:
            if idx < len(row):
                cell = row[idx].strip()
                num = _to_number(cell)
                if num is not None:
                    values.append(num)
        # Treat a column as numeric only if every non-empty cell parsed.
        non_empty = sum(
            1 for row in data_rows if idx < len(row) and row[idx].strip()
        )
        if values and len(values) == non_empty:
            numeric_stats[col] = {
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "mean": round(sum(values) / len(values), 4),
            }

    return {
        "kind": "csv",
        "row_count": len(data_rows),
        "column_count": len(columns),
        "columns": columns,
        "numeric_stats": numeric_stats,
    }


def extract_text(content: str) -> dict[str, Any]:
    """Summarise plain text: counts and top keyword frequencies."""
    if not content.strip():
        raise ExtractionError("text document is empty")

    lines = content.splitlines()
    words = _WORD_RE.findall(content.lower())
    keywords = Counter(w for w in words if w not in _STOPWORDS and len(w) > 2)

    return {
        "kind": "text",
        "line_count": len(lines),
        "word_count": len(words),
        "char_count": len(content),
        "top_keywords": [
            {"word": word, "count": count}
            for word, count in keywords.most_common(10)
        ],
    }


def _to_number(cell: str) -> float | None:
    """Parse a cell as a number, tolerating thousands separators. None if not."""
    if not cell:
        return None
    try:
        return float(cell.replace(",", ""))
    except ValueError:
        return None
