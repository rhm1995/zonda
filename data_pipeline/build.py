"""CLI entry point for the offline data build pipeline (TASK-001, design
§8.4).

    python -m data_pipeline.build \\
        --newbuild data/raw/newbuild.xlsx \\
        --existing data/raw/existing.xlsx \\
        --out data/processed/

Exit code 0 and a written `BUILD_INFO.json` on success; a non-zero exit
with a specific diagnostic (missing sheet, wrong column count, new
suppression marker, spot-check mismatch) on failure. This script is run
once at development time — its bundled output is what ships (ADR-004); the
running app never invokes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from data_pipeline.parse_ons_workbook import WorkbookStructureError, parse_workbook
from data_pipeline.validate import (
    validate_matching_axes,
    validate_single_workbook,
    validate_spot_checks,
)

EDITION = "Year ending September 2025"

SOURCE_URLS = {
    "new_build": (
        "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/housing/"
        "datasets/medianhousepricesforadministrativegeographiesnewlybuiltdwellings/"
        "yearendingseptember2025/medianpricepaidforadministrativegeographiesnew.xlsx"
    ),
    "existing": (
        "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/housing/"
        "datasets/medianhousepricesforadministrativegeographiesexistingdwellings/"
        "yearendingseptember2025/medianpricepaidforadministrativegeographiesexisting.xlsx"
    ),
}

# Common Scotland/Northern Ireland place names likely to appear in a
# free-text question (design §6.4) — neither dataset covers them (§6.1).
# Not consumed by Increment 1 (the deterministic tabs use closed selectors,
# ADR-012); built now for TASK-011's later use, per TASK-001's scope.
OUT_OF_COVERAGE_PLACES = [
    "Scotland",
    "Northern Ireland",
    "Glasgow",
    "Edinburgh",
    "Aberdeen",
    "Dundee",
    "Belfast",
    "Inverness",
    "Stirling",
    "Perth",
    "Derry",
    "Londonderry",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _price_points_frame(price_points) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "dataset": p.dataset,
                "region_country_code": p.region_country_code,
                "region_country_name": p.region_country_name,
                "la_code": p.la_code,
                "la_name": p.la_name,
                "period_label": p.period_label,
                "period_end_date": p.period_end_date,
                "price_gbp": p.price_gbp,
                "suppressed": p.suppressed,
            }
            for p in price_points
        ]
    )
    # Nullable integer dtype: a price is always a whole number of GBP, and a
    # plain int64 column can't represent the suppressed (None) rows without
    # silently widening to float64 -- not appropriate for a money field.
    frame["price_gbp"] = frame["price_gbp"].astype("Int64")
    return frame


def build(newbuild_path: Path, existing_path: Path, out_dir: Path) -> None:
    new_build = parse_workbook(newbuild_path, "new_build")
    existing = parse_workbook(existing_path, "existing")

    validate_single_workbook(new_build)
    validate_single_workbook(existing)
    validate_matching_axes(new_build, existing)
    validate_spot_checks(new_build, existing)

    all_points = new_build.price_points + existing.price_points
    prices_frame = _price_points_frame(all_points)

    # Geography reference: identical LA set in both files (validated above)
    # -- built from new_build's list, deduplicated by la_code for safety.
    geography_by_code = {la.la_code: la for la in new_build.local_authorities}
    geography_frame = pd.DataFrame(
        [
            {
                "la_code": la.la_code,
                "la_name": la.la_name,
                "region_country_code": la.region_country_code,
                "region_country_name": la.region_country_name,
                "aliases": la.aliases,
            }
            for la in geography_by_code.values()
        ]
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    prices_tmp = out_dir / ".detached_house_prices.parquet.tmp"
    geography_tmp = out_dir / ".geography_reference.parquet.tmp"
    prices_frame.to_parquet(prices_tmp, index=False)
    geography_frame.to_parquet(geography_tmp, index=False)
    prices_tmp.replace(out_dir / "detached_house_prices.parquet")
    geography_tmp.replace(out_dir / "geography_reference.parquet")

    _atomic_write_bytes(
        out_dir / "out_of_coverage_places.json",
        json.dumps(OUT_OF_COVERAGE_PLACES, indent=2).encode("utf-8"),
    )

    build_info = {
        "source": {
            "new_build": {
                "url": SOURCE_URLS["new_build"],
                "filename": newbuild_path.name,
                "sha256": _sha256(newbuild_path),
            },
            "existing": {
                "url": SOURCE_URLS["existing"],
                "filename": existing_path.name,
                "sha256": _sha256(existing_path),
            },
        },
        "edition": EDITION,
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "local_authorities": len(geography_by_code),
            "periods": len(new_build.period_labels),
            "price_points": len(all_points),
        },
    }
    _atomic_write_bytes(
        out_dir / "BUILD_INFO.json",
        json.dumps(build_info, indent=2).encode("utf-8"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--newbuild", type=Path, required=True)
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        build(args.newbuild, args.existing, args.out)
    except WorkbookStructureError as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Build succeeded. Output written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
