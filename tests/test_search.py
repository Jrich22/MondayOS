"""Tests for the search module."""
from __future__ import annotations

import pytest

from search import SearchEngine, SearchQuery, SearchResult


class TestSearchQuery:
    def test_default_limit_is_10(self) -> None:
        q = SearchQuery(text="rate limit")
        assert q.limit == 10

    def test_default_sources_is_empty(self) -> None:
        q = SearchQuery(text="memory leak")
        assert q.sources == []

    def test_default_tags_is_empty(self) -> None:
        assert SearchQuery(text="x").tags == []

    def test_default_components_is_empty(self) -> None:
        assert SearchQuery(text="x").components == []


class TestSearchResult:
    def _make_result(self, score: float, entry_id: str = "X-0001") -> SearchResult:
        return SearchResult(
            source="knowledge",
            entry_id=entry_id,
            title="Test result",
            snippet="relevant excerpt...",
            score=score,
        )

    def test_higher_score_sorts_before_lower(self) -> None:
        high = self._make_result(0.9, "BUG-0001")
        low = self._make_result(0.2, "BUG-0002")
        assert high < low  # high score → sorts first → less-than in sort order

    def test_sorted_results_are_highest_first(self) -> None:
        results = [
            self._make_result(0.3),
            self._make_result(0.9),
            self._make_result(0.5),
        ]
        ranked = sorted(results)
        assert ranked[0].score == 0.9
        assert ranked[-1].score == 0.3

    def test_default_metadata_is_empty(self) -> None:
        result = self._make_result(0.5)
        assert result.metadata == {}


class TestSearchEngine:
    def test_search_not_yet_implemented(self) -> None:
        engine = SearchEngine()
        with pytest.raises(NotImplementedError):
            engine.search(SearchQuery(text="anything"))

    def test_index_source_not_yet_implemented(self) -> None:
        engine = SearchEngine()
        with pytest.raises(NotImplementedError):
            engine.index_source("knowledge", [])

    # TODO: The tests below define expected behavior once SearchEngine is implemented.

    def test_search_returns_results_ranked_by_score(self) -> None:
        pytest.skip("TODO: implement SearchEngine.search()")

    def test_search_respects_limit(self) -> None:
        pytest.skip("TODO: implement limit in SearchEngine.search()")

    def test_search_filters_by_source(self) -> None:
        pytest.skip("TODO: implement source filtering in SearchEngine.search()")

    def test_search_empty_query_returns_empty(self) -> None:
        pytest.skip("TODO: define behavior for empty query string")

    def test_search_filters_by_tags(self) -> None:
        pytest.skip("TODO: implement tag filtering in SearchEngine.search()")
