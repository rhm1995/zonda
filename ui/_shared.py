"""Shared, Streamlit-cached data access for the two deterministic tabs
(`ui/explore_trends.py`, `ui/compare_rank.py`).

Not one of the design's three named tab modules -- a small private helper
factored out to avoid duplicating the repository-open/geography/period
caching between them (DRY). Imports only `core` and `streamlit`, same as
its callers -- never `agent`/`openai`/`agents` (ADR-011).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.models import Period
from core.repository import Repository

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


@st.cache_resource
def get_repository() -> Repository:
    return Repository.open(PROCESSED_DIR)


@st.cache_data
def load_areas(_repository: Repository) -> list[tuple[str, str]]:
    """Returns (la_code, la_name) pairs -- a plain, hashable-friendly shape
    for `st.cache_data` (the repository param is excluded from hashing by
    its leading underscore, Streamlit's convention for unhashable
    dependencies)."""
    return [(la.la_code, la.la_name) for la in _repository.get_geography_reference()]


@st.cache_data
def load_periods(_repository: Repository) -> list[Period]:
    return _repository.get_period_reference()
