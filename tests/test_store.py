"""State store: revision 0 on create, valid YAML after atomic save, revision checking."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from memory_core.errors import ChallengeExists, ChallengeNotFound, RevisionConflict, StateCorrupt
from memory_core.models import (
    ChallengeMeta,
    CreateChallengeRequest,
    FactItem,
    GuessingState,
    MemorySnapshot,
)
from memory_core.service import MemoryService
from memory_core.store import StateStore


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


def test_corrupt_state_is_reported_not_guessed(store: StateStore, runs_dir: Path) -> None:
    store.init(_snapshot())
    (runs_dir / "demo" / "state.yaml").write_text("revision: not-an-int\n", encoding="utf-8")
    with pytest.raises(StateCorrupt):
        store.load("demo")
