from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


STANDARD_COLUMNS = ["person_id", "time", "lon", "lat", "mode", "purpose"]


def resolve_path(project_root: Path, path_text: str) -> Path:
    """Resolve a path relative to the project root unless it is absolute."""
    path = Path(path_text)
    return path if path.is_absolute() else project_root / path


def load_csv(path: Path, config: dict[str, Any]) -> pd.DataFrame:
    """Load a person-flow CSV and normalize configured columns to standard names."""
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    csv_config = config.get("csv", {})
    read_kwargs = {
        "encoding": csv_config.get("encoding", "utf-8"),
    }
    if not csv_config.get("has_header", True):
        read_kwargs["header"] = None

    df = pd.read_csv(path, **read_kwargs)
    columns = config["columns"]

    normalized = pd.DataFrame()
    for standard in STANDARD_COLUMNS:
        if standard not in columns:
            raise ValueError(f"Missing column mapping for: {standard}")
        normalized[standard] = _get_mapped_series(df, columns[standard], standard)

    df = normalized
    validate_columns(df)

    df["time"] = pd.to_datetime(df["time"], format=config["time"].get("format"), errors="coerce")
    if df["time"].isna().any():
        bad_count = int(df["time"].isna().sum())
        raise ValueError(f"Failed to parse {bad_count} time values. Check config time.format.")

    for col in ["lon", "lat"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["lon", "lat"]].isna().any().any():
        raise ValueError("lon/lat contain non-numeric or missing values.")

    return df[STANDARD_COLUMNS].copy()


def _get_mapped_series(df: pd.DataFrame, source: str | int, standard: str) -> pd.Series:
    """Return a source column by name or zero-based index."""
    if isinstance(source, int):
        if source >= len(df.columns) or source < 0:
            raise ValueError(f"Column index for {standard} is out of range: {source}")
        return df.iloc[:, source]

    if isinstance(source, str) and source.isdigit() and source not in df.columns:
        index = int(source)
        if index >= len(df.columns):
            raise ValueError(f"Column index for {standard} is out of range: {index}")
        return df.iloc[:, index]

    if source not in df.columns:
        raise ValueError(f"Missing source column for {standard}: {source}")
    return df[source]


def validate_columns(df: pd.DataFrame) -> None:
    """Validate that standard columns are present after renaming."""
    missing = [col for col in STANDARD_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns after mapping: {missing}")


def sort_by_person_time(df: pd.DataFrame) -> pd.DataFrame:
    """Sort records by person_id and time."""
    return df.sort_values(["person_id", "time"]).reset_index(drop=True)


def generate_sample_data(config: dict[str, Any], output_path: Path) -> pd.DataFrame:
    """Generate simple sample trajectories between station and campus."""
    sample = config["sample"]
    rng = np.random.default_rng(config.get("random_seed", 42))

    n_persons = int(sample.get("n_persons", 120))
    points_per_person = int(sample.get("points_per_person", 8))
    start_time = pd.Timestamp(sample.get("start_time", "2026-05-11 08:00:00"))
    interval = int(sample.get("interval_minutes", 5))

    station = config["pois"]["station"]
    campus = config["pois"]["campus"]
    records = []
    modes = np.array(["walk", "walk", "walk", "bus", "bike"])
    purposes = np.array(["commute", "study", "work"])

    for person_idx in range(n_persons):
        reverse = rng.random() < 0.18
        start_lon, start_lat = (campus["lon"], campus["lat"]) if reverse else (station["lon"], station["lat"])
        end_lon, end_lat = (station["lon"], station["lat"]) if reverse else (campus["lon"], campus["lat"])
        person_start = start_time + pd.Timedelta(minutes=int(rng.integers(0, 90)))
        mode = str(rng.choice(modes))
        purpose = str(rng.choice(purposes))

        for point_idx in range(points_per_person):
            t = point_idx / max(points_per_person - 1, 1)
            lon = start_lon + (end_lon - start_lon) * t + rng.normal(0, 0.00045)
            lat = start_lat + (end_lat - start_lat) * t + rng.normal(0, 0.00035)
            records.append(
                {
                    "person_id": f"p{person_idx:04d}",
                    "time": person_start + pd.Timedelta(minutes=point_idx * interval),
                    "lon": lon,
                    "lat": lat,
                    "mode": mode,
                    "purpose": purpose,
                }
            )

    df = pd.DataFrame(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return df
