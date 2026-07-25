"""State store: revision 0 on create, valid YAML after atomic save, revision checking."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from memory.core.errors import (
    ChallengeExists,
    ChallengeNotFound,
    InvalidRequest,
    RevisionConflict,
    StateCorrupt,
)
from memory.core.journal import Journal
from memory.core.models import (
    ChallengeMeta,
    CreateChallengeRequest,
    FactItem,
    GuessingState,
    MemorySnapshot,
)
from memory.core.service import MemoryService
from memory.core.store import StateStore


def _snapshot(challenge_id: str = "demo") -> MemorySnapshot:
    return MemorySnapshot(
        challenge=ChallengeMeta(id=challenge_id, title="Demo"),
        fact={"general": []},
        assumption={"general": []},
        guessing=GuessingState(catalog="generic@1"),
    )


def test_create_challenge_starts_at_revision_zero(service: MemoryService) -> None:
    response = service.create_challenge(CreateChallengeRequest(id="c1", title="C1"))
    assert response.revision == 0
    assert service.read_memory("c1").revision == 0


def test_duplicate_challenge_is_rejected(store: StateStore) -> None:
    store.init(_snapshot())
    with pytest.raises(ChallengeExists):
        store.init(_snapshot())


def test_missing_challenge_raises_not_found(store: StateStore) -> None:
    with pytest.raises(ChallengeNotFound):
        store.load("nope")


def test_atomic_save_leaves_valid_yaml(store: StateStore, runs_dir: Path) -> None:
    snapshot = _snapshot()
    snapshot.fact["general"].append(FactItem(id="F001", text="TCP/80 is open."))
    store.init(snapshot)

    path = runs_dir / "demo" / "state.yaml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert parsed["revision"] == 0
    assert parsed["fact"]["general"][0]["id"] == "F001"
    # Round-trips back into a valid model, and no temp files were left behind.
    assert MemorySnapshot.model_validate(parsed).revision == 0
    assert not list((runs_dir / "demo").glob(".state-*"))


def test_commit_increments_revision(store: StateStore) -> None:
    store.init(_snapshot())
    before, after = store.commit("demo", 0, lambda s: s)
    assert (before.revision, after.revision) == (0, 1)
    assert store.load("demo").revision == 1


def test_stale_expected_revision_conflicts(store: StateStore) -> None:
    store.init(_snapshot())
    store.commit("demo", 0, lambda s: s)  # now at revision 1
    with pytest.raises(RevisionConflict) as excinfo:
        store.commit("demo", 0, lambda s: s)
    assert excinfo.value.details["current_revision"] == 1
    assert excinfo.value.status == 409


@pytest.mark.parametrize("challenge_id", ["..", ".", ".hidden", "../escape"])
def test_a_challenge_id_may_not_be_a_path(
    store: StateStore, journal: Journal, runs_dir: Path, challenge_id: str
) -> None:
    """A challenge id names a directory under ``runs/``; it must not be able to leave it.

    ``..`` is the dangerous one: it passed the old id pattern, resolved to the parent of
    ``runs/``, and produced a challenge that ``list_challenges`` could not even see.
    """

    with pytest.raises(InvalidRequest):
        store.init(_snapshot(challenge_id))
    with pytest.raises(InvalidRequest):
        store.load(challenge_id)
    with pytest.raises(InvalidRequest):
        journal.ensure(challenge_id)

    assert not (runs_dir.parent / "state.yaml").exists()
    assert not (runs_dir.parent / "journal.jsonl").exists()
    assert list(runs_dir.iterdir()) == []


def test_the_request_model_rejects_a_path_shaped_id() -> None:
    """The same rule, enforced in the published contract rather than only in the store."""

    for challenge_id in ("..", ".", ".hidden"):
        with pytest.raises(ValidationError):
            CreateChallengeRequest(id=challenge_id, title="escape")
    # Ordinary ids keep working, dots included, as long as the id is not a path.
    assert CreateChallengeRequest(id="demo-web.v2", title="fine").id == "demo-web.v2"


def test_corrupt_state_is_reported_not_guessed(store: StateStore, runs_dir: Path) -> None:
    store.init(_snapshot())
    (runs_dir / "demo" / "state.yaml").write_text("revision: not-an-int\n", encoding="utf-8")
    with pytest.raises(StateCorrupt):
        store.load("demo")
