"""Build-time validation for the ingested ONS workbooks (TASK-001, design
§6.4). Every assertion here fails the build loudly and specifically — the
build must never produce a silently partial or silently wrong snapshot
(RSK-003).
"""

from __future__ import annotations

from data_pipeline.parse_ons_workbook import ParsedWorkbook, WorkbookStructureError

EXPECTED_LOCAL_AUTHORITY_COUNT = 318
EXPECTED_PERIOD_COUNT = 120

# Confirmed spot-check values (design §6.1), re-checked on every build as a
# concrete regression fixture against ONS changing the file layout/values
# without notice.
SPOT_CHECKS: dict[tuple[str, str, str], int] = {
    ("Manchester", "new_build", "Year ending Sep 2025"): 495000,
    ("Manchester", "new_build", "Year ending Sep 2015"): 177995,
    ("Manchester", "existing", "Year ending Sep 2025"): 400000,
    ("Manchester", "existing", "Year ending Sep 2015"): 220000,
}


def validate_single_workbook(parsed: ParsedWorkbook) -> None:
    """Structural checks that apply to one parsed workbook in isolation."""
    if len(parsed.local_authorities) != EXPECTED_LOCAL_AUTHORITY_COUNT:
        raise WorkbookStructureError(
            f"{parsed.dataset}: expected {EXPECTED_LOCAL_AUTHORITY_COUNT} "
            f"local authorities, found {len(parsed.local_authorities)}"
        )
    if len(parsed.period_labels) != EXPECTED_PERIOD_COUNT:
        raise WorkbookStructureError(
            f"{parsed.dataset}: expected {EXPECTED_PERIOD_COUNT} periods, "
            f"found {len(parsed.period_labels)}"
        )
    expected_points = EXPECTED_LOCAL_AUTHORITY_COUNT * EXPECTED_PERIOD_COUNT
    if len(parsed.price_points) != expected_points:
        raise WorkbookStructureError(
            f"{parsed.dataset}: expected {expected_points} price points "
            f"({EXPECTED_LOCAL_AUTHORITY_COUNT} areas x {EXPECTED_PERIOD_COUNT} "
            f"periods), found {len(parsed.price_points)}"
        )


def validate_matching_axes(new_build: ParsedWorkbook, existing: ParsedWorkbook) -> None:
    """The two files must share identical period and geography axes —
    required for `get_premium_series`'s join (design §6.7) to ever produce
    a complete pair."""
    if new_build.period_labels != existing.period_labels:
        raise WorkbookStructureError(
            "new-build and existing workbooks have different period axes "
            "(order or content) — premium joins would silently misalign"
        )
    nb_codes = {la.la_code for la in new_build.local_authorities}
    ex_codes = {la.la_code for la in existing.local_authorities}
    if nb_codes != ex_codes:
        only_nb = sorted(nb_codes - ex_codes)[:5]
        only_ex = sorted(ex_codes - nb_codes)[:5]
        raise WorkbookStructureError(
            "new-build and existing workbooks cover different local "
            f"authorities — only in new-build: {only_nb}, only in existing: {only_ex}"
        )


def validate_spot_checks(new_build: ParsedWorkbook, existing: ParsedWorkbook) -> None:
    """Re-check the confirmed §6.1 spot-check figures against the actual
    parsed output, as a concrete regression fixture."""
    by_dataset = {"new_build": new_build, "existing": existing}
    for (la_name, dataset, period_label), expected_price in SPOT_CHECKS.items():
        parsed = by_dataset[dataset]
        matches = [
            point
            for point in parsed.price_points
            if point.la_name == la_name and point.period_label == period_label
        ]
        if len(matches) != 1:
            raise WorkbookStructureError(
                f"Spot-check lookup failed: {la_name!r}/{dataset}/{period_label!r} "
                f"matched {len(matches)} rows, expected exactly 1"
            )
        actual_price = matches[0].price_gbp
        if actual_price != expected_price:
            raise WorkbookStructureError(
                f"Spot-check mismatch: {la_name!r}/{dataset}/{period_label!r} "
                f"expected {expected_price}, got {actual_price!r}"
            )
