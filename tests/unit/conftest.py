from __future__ import annotations

from pathlib import Path

import pytest

from core.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"


@pytest.fixture(scope="session")
def real_repository() -> Repository:
    """The repository opened against the real, bundled processed snapshot
    (built by `python -m data_pipeline.build`) -- used for tests that check
    behaviour against the confirmed §6.1 spot-check figures. Component
    tests for `core/repository.py` itself use small hand-authored fixtures
    instead (tests/unit/test_repository.py), per the stakeholder's explicit
    testing-approach instruction."""
    if not PROCESSED_DIR.exists():
        pytest.skip(
            "data/processed/ not built -- run `python -m data_pipeline.build` first"
        )
    return Repository.open(PROCESSED_DIR)
