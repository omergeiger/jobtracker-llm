"""C3 - reranker output validation (verifiable, offline).

The listwise reranker asks Claude to order posts shown under short labels. Model output is
untrusted: it can drop posts, repeat them, invent labels or break the rank sequence. These
tests feed such replies through the real mapping + validation code with the CLI mocked out.
"""

import pytest

import scoring.reranker as rr

ID_MAP = {"A1": "uuid-aaa", "A2": "uuid-bbb", "A3": "uuid-ccc"}


def reply(*pairs):
    """pairs of (label, rank) -> a fake `claude -p` JSON response."""
    return {"structured_output": {"rankings": [
        {"post_id": label, "rank": rank, "rationale": "x"} for label, rank in pairs
    ]}}


def validate(fake):
    rankings = rr._map_short_ids(fake["structured_output"]["rankings"], ID_MAP)
    rr._validate_rankings(rankings, set(ID_MAP.values()))
    return rankings


def test_valid_reply_maps_labels_back_to_ids():
    out = validate(reply(("A2", 1), ("a1", 2), ("[A3]", 3)))   # case / bracket tolerant
    assert [r["post_id"] for r in out] == ["uuid-bbb", "uuid-aaa", "uuid-ccc"]


@pytest.mark.parametrize("bad", [
    reply(("A1", 1), ("A2", 2)),                        # dropped a post
    reply(("A1", 1), ("A1", 2), ("A2", 3)),             # duplicate
    reply(("A1", 1), ("A2", 2), ("Z9", 3)),             # invented label
    reply(("A1", 1), ("A2", 2), ("A3", 5)),             # rank sequence broken
    reply(("A1", 1), ("A2", 1), ("A3", 3)),             # tied ranks
])
def test_invalid_replies_are_rejected(bad):
    with pytest.raises(ValueError):
        validate(bad)


def test_retry_recovers_from_one_bad_reply(monkeypatch):
    replies = iter([reply(("A1", 1), ("A2", 2)), reply(("A3", 1), ("A1", 2), ("A2", 3))])
    monkeypatch.setattr(rr, "_invoke_claude_cli", lambda *a, **k: next(replies))
    out = rr._invoke_and_validate("haiku", "sys", "msg", ID_MAP, max_attempts=2)
    assert [r["post_id"] for r in out] == ["uuid-ccc", "uuid-aaa", "uuid-bbb"]


def test_persistent_bad_replies_fail_loudly(monkeypatch):
    monkeypatch.setattr(rr, "_invoke_claude_cli", lambda *a, **k: reply(("A1", 1)))
    with pytest.raises(RuntimeError):
        rr._invoke_and_validate("haiku", "sys", "msg", ID_MAP, max_attempts=2)
