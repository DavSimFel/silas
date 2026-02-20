from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from silas.context.retriever import SilasMemoryRetriever
from silas.models.agents import MemoryQuery, MemoryQueryStrategy
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import TaintLevel

pytestmark = pytest.mark.asyncio


def _item(memory_id: str, content: str, session_id: str | None = None) -> MemoryItem:
    return MemoryItem(
        memory_id=memory_id,
        content=content,
        memory_type=MemoryType.fact,
        taint=TaintLevel.owner,
        session_id=session_id,
        source_kind="test",
    )


@dataclass(slots=True)
class RecordingMemoryStore:
    keyword_results: list[MemoryItem] = field(default_factory=list)
    temporal_results: list[MemoryItem] = field(default_factory=list)
    session_results_by_id: dict[str, list[MemoryItem]] = field(default_factory=dict)
    raw_results: list[MemoryItem] = field(default_factory=list)
    keyword_calls: list[tuple[str, int]] = field(default_factory=list)
    temporal_calls: list[int] = field(default_factory=list)
    session_calls: list[str] = field(default_factory=list)
    raw_calls: list[tuple[str, int]] = field(default_factory=list)

    async def store(self, item: MemoryItem) -> str:
        return item.memory_id

    async def get(self, memory_id: str) -> MemoryItem | None:
        del memory_id
        return None

    async def update(self, memory_id: str, **kwargs: object) -> None:
        del memory_id, kwargs

    async def delete(self, memory_id: str) -> None:
        del memory_id

    async def search_keyword(self, query: str, limit: int) -> list[MemoryItem]:
        self.keyword_calls.append((query, limit))
        return self.keyword_results[:limit]

    async def search_by_type(self, memory_type: MemoryType, limit: int) -> list[MemoryItem]:
        del memory_type, limit
        return []

    async def list_recent(self, limit: int) -> list[MemoryItem]:
        self.temporal_calls.append(limit)
        return self.temporal_results[:limit]

    async def increment_access(self, memory_id: str) -> None:
        del memory_id

    async def search_session(self, session_id: str) -> list[MemoryItem]:
        self.session_calls.append(session_id)
        return list(self.session_results_by_id.get(session_id, []))

    async def store_raw(self, item: MemoryItem) -> str:
        return item.memory_id

    async def search_raw(self, query: str, limit: int) -> list[MemoryItem]:
        self.raw_calls.append((query, limit))
        return self.raw_results[:limit]


async def test_retrieve_keyword_dispatches_to_keyword_search() -> None:
    store = RecordingMemoryStore(
        keyword_results=[_item("k1", "python retriever"), _item("k2", "python runtime")]
    )
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(strategy=MemoryQueryStrategy.keyword, query="python", max_results=1)

    results = await retriever.retrieve(query)

    assert [item.memory_id for item in results] == ["k1"]
    assert store.keyword_calls == [("python", 1)]
    assert store.temporal_calls == []
    assert store.session_calls == []
    assert store.raw_calls == []


async def test_retrieve_temporal_dispatches_to_list_recent() -> None:
    store = RecordingMemoryStore(
        temporal_results=[_item("t1", "first"), _item("t2", "second"), _item("t3", "third")]
    )
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(strategy=MemoryQueryStrategy.temporal, query="ignored", max_results=2)

    results = await retriever.retrieve(query)

    assert [item.memory_id for item in results] == ["t1", "t2"]
    assert store.temporal_calls == [2]
    assert store.keyword_calls == []
    assert store.session_calls == []
    assert store.raw_calls == []


async def test_retrieve_session_prefers_explicit_session_id() -> None:
    store = RecordingMemoryStore(
        session_results_by_id={
            "explicit-session": [_item("s1", "explicit memory", session_id="explicit-session")],
            "query-session": [_item("s2", "query memory", session_id="query-session")],
        }
    )
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(strategy=MemoryQueryStrategy.session, query="query-session", max_results=5)

    results = await retriever.retrieve(query, session_id="explicit-session")

    assert [item.memory_id for item in results] == ["s1"]
    assert store.session_calls == ["explicit-session"]


async def test_retrieve_session_falls_back_to_query_text() -> None:
    store = RecordingMemoryStore(
        session_results_by_id={"session-from-query": [_item("s3", "query fallback")]}
    )
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(strategy=MemoryQueryStrategy.session, query="session-from-query")

    results = await retriever.retrieve(query)

    assert [item.memory_id for item in results] == ["s3"]
    assert store.session_calls == ["session-from-query"]


async def test_retrieve_session_falls_back_to_scope_id_when_query_is_blank() -> None:
    store = RecordingMemoryStore(
        session_results_by_id={"scope-session": [_item("s4", "scope fallback", "scope-session")]}
    )
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(strategy=MemoryQueryStrategy.session, query="   ")

    results = await retriever.retrieve(query, scope_id="scope-session")

    assert [item.memory_id for item in results] == ["s4"]
    assert store.session_calls == ["scope-session"]


async def test_retrieve_session_returns_empty_when_no_session_hint_exists() -> None:
    store = RecordingMemoryStore()
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(strategy=MemoryQueryStrategy.session, query="   ")

    results = await retriever.retrieve(query)

    assert results == []
    assert store.session_calls == []


async def test_retrieve_semantic_uses_raw_lane_when_available() -> None:
    store = RecordingMemoryStore(
        raw_results=[_item("r1", "raw memory")],
        keyword_results=[_item("k1", "keyword memory")],
    )
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(strategy=MemoryQueryStrategy.semantic, query="memory")

    results = await retriever.retrieve(query)

    assert [item.memory_id for item in results] == ["r1"]
    assert store.raw_calls == [("memory", 5)]
    assert store.keyword_calls == []


async def test_retrieve_semantic_falls_back_to_keyword_when_raw_is_empty() -> None:
    store = RecordingMemoryStore(
        raw_results=[], keyword_results=[_item("k2", "keyword fallback memory")]
    )
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(strategy=MemoryQueryStrategy.semantic, query="fallback", max_results=3)

    results = await retriever.retrieve(query)

    assert [item.memory_id for item in results] == ["k2"]
    assert store.raw_calls == [("fallback", 3)]
    assert store.keyword_calls == [("fallback", 3)]


async def test_retrieve_applies_token_budget_and_stops_when_budget_exceeded() -> None:
    # Three items at ~4 estimated tokens each; budget 8 should keep only two.
    store = RecordingMemoryStore(
        keyword_results=[
            _item("k1", "a" * 16),
            _item("k2", "b" * 16),
            _item("k3", "c" * 16),
        ]
    )
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(
        strategy=MemoryQueryStrategy.keyword,
        query="token-budget",
        max_results=5,
        max_tokens=8,
    )

    results = await retriever.retrieve(query)

    assert [item.memory_id for item in results] == ["k1", "k2"]


async def test_retrieve_returns_empty_when_token_budget_is_zero_or_negative() -> None:
    store = RecordingMemoryStore(keyword_results=[_item("k1", "this should not pass budget")])
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(
        strategy=MemoryQueryStrategy.keyword,
        query="zero-budget",
        max_results=5,
        max_tokens=0,
    )

    results = await retriever.retrieve(query)

    assert results == []


async def test_retrieve_uses_len_div_4_token_estimation_for_short_content() -> None:
    # len("abc") // 4 == 0, so it should not consume budget before the second item.
    store = RecordingMemoryStore(keyword_results=[_item("k1", "abc"), _item("k2", "wxyz")])
    retriever = SilasMemoryRetriever(store=store)
    query = MemoryQuery(
        strategy=MemoryQueryStrategy.keyword,
        query="short-content",
        max_results=5,
        max_tokens=1,
    )

    results = await retriever.retrieve(query)

    assert [item.memory_id for item in results] == ["k1", "k2"]
