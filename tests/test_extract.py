"""Unit tests for the deterministic extraction logic (no AWS)."""
from __future__ import annotations

import pytest

from worker.extract import ExtractionError, extract, extract_csv, extract_text


class TestCsv:
    def test_basic_shape(self):
        content = "name,age,score\nalice,30,9.5\nbob,25,7.0\n"
        result = extract_csv(content)
        assert result["kind"] == "csv"
        assert result["row_count"] == 2
        assert result["column_count"] == 3
        assert result["columns"] == ["name", "age", "score"]

    def test_numeric_stats_only_for_numeric_columns(self):
        content = "name,age,score\nalice,30,9.5\nbob,25,7.0\n"
        stats = extract_csv(content)["numeric_stats"]
        assert set(stats) == {"age", "score"}  # "name" is not numeric
        assert stats["age"] == {"count": 2, "min": 25.0, "max": 30.0, "mean": 27.5}
        assert stats["score"]["mean"] == 8.25

    def test_column_with_mixed_types_is_not_numeric(self):
        content = "id,note\n1,ok\n2,fail\n"
        stats = extract_csv(content)["numeric_stats"]
        assert "note" not in stats
        assert "id" in stats

    def test_thousands_separators_parsed(self):
        content = "city,population\nx,\"1,000\"\ny,\"2,500\"\n"
        stats = extract_csv(content)["numeric_stats"]
        assert stats["population"]["min"] == 1000.0
        assert stats["population"]["max"] == 2500.0

    def test_empty_raises(self):
        with pytest.raises(ExtractionError):
            extract_csv("   \n  ")

    def test_header_only_has_zero_rows(self):
        result = extract_csv("a,b,c\n")
        assert result["row_count"] == 0
        assert result["numeric_stats"] == {}


class TestText:
    def test_counts(self):
        content = "Hello world.\nThis is a test document.\n"
        result = extract_text(content)
        assert result["kind"] == "text"
        assert result["line_count"] == 2
        assert result["char_count"] == len(content)
        assert result["word_count"] > 0

    def test_keywords_exclude_stopwords(self):
        content = "cloud cloud cloud the the a an system system"
        keywords = {k["word"]: k["count"] for k in extract_text(content)["top_keywords"]}
        assert keywords["cloud"] == 3
        assert keywords["system"] == 2
        assert "the" not in keywords  # stopword filtered

    def test_empty_raises(self):
        with pytest.raises(ExtractionError):
            extract_text("")


class TestDispatch:
    def test_dispatch_csv(self):
        assert extract("a,b\n1,2\n", "csv")["kind"] == "csv"

    def test_dispatch_text_aliases(self):
        for alias in ("text", "txt", "plain", "TEXT"):
            assert extract("hello world", alias)["kind"] == "text"

    def test_unknown_type_raises(self):
        with pytest.raises(ExtractionError):
            extract("data", "pdf")
