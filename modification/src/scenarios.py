from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from coordinates import distance_m_to_point


def baseline(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return baseline records with a scenario label."""
    out = gdf.copy()
    out["scenario"] = "baseline"
    return out


def apply_rain_scenario(gdf: gpd.GeoDataFrame, config: dict[str, Any], rng: np.random.Generator) -> gpd.GeoDataFrame:
    """Apply rain assumptions: walk-to-bus conversion, delay, and dwell duplication."""
    params = config["scenarios"]["rain"]
    pois = config["pois"]
    crs = config["coordinates"]["projected_crs"]
    out = gdf.copy()
    out["scenario"] = "rain"

    walk_mask = out["mode"].eq("walk")
    convert_persons = _sample_person_ids(out[walk_mask], params["walk_to_bus_ratio"], rng)
    convert_mask = out["person_id"].isin(convert_persons) & walk_mask
    out.loc[convert_mask, "mode"] = "bus"
    out.loc[walk_mask, "time"] = out.loc[walk_mask, "time"] + pd.to_timedelta(params["walk_delay_minutes"], unit="m")

    station_mask = distance_m_to_point(out, pois["station"]["lon"], pois["station"]["lat"], crs) <= pois["station"]["radius_m"]
    campus_mask = distance_m_to_point(out, pois["campus"]["lon"], pois["campus"]["lat"], crs) <= pois["campus"]["radius_m"]
    dwell_candidates = out[station_mask | campus_mask]
    duplicated = _duplicate_points(
        dwell_candidates,
        ratio=params["dwell_duplicate_ratio"],
        duplicate_count=int(params["dwell_duplicate_count"]),
        rng=rng,
        minute_step=1,
    )
    return _combine_sort(out, duplicated)


def apply_shuttle_scenario(gdf: gpd.GeoDataFrame, config: dict[str, Any], rng: np.random.Generator) -> gpd.GeoDataFrame:
    """Apply shuttle assumptions for station-to-campus travelers."""
    params = config["scenarios"]["shuttle"]
    pois = config["pois"]
    crs = config["coordinates"]["projected_crs"]
    out = gdf.copy()
    out["scenario"] = "shuttle"

    eligible = _station_to_campus_person_ids(out, config)
    selected = _sample_ids(eligible, params["eligible_ratio"], rng)
    selected_mask = out["person_id"].isin(selected)
    out.loc[selected_mask & out["mode"].isin(["walk", "bus"]), "mode"] = "shuttle"

    first_times = out.groupby("person_id")["time"].transform("min")
    after_start_mask = selected_mask & (out["time"] > first_times)
    out.loc[after_start_mask, "time"] = out.loc[after_start_mask, "time"] - pd.to_timedelta(
        params["time_reduction_minutes"], unit="m"
    )

    campus_mask = distance_m_to_point(out, pois["campus_entrance"]["lon"], pois["campus_entrance"]["lat"], crs) <= pois[
        "campus_entrance"
    ]["radius_m"]
    arrivals = out[selected_mask & campus_mask]
    boosted = _duplicate_points(arrivals, ratio=params["arrival_boost_ratio"], duplicate_count=1, rng=rng, minute_step=1)
    return _combine_sort(out, boosted)


def apply_poi_scenario(gdf: gpd.GeoDataFrame, config: dict[str, Any], rng: np.random.Generator) -> gpd.GeoDataFrame:
    """Add visits and dwell points around a new campus entrance POI."""
    params = config["scenarios"]["poi"]
    pois = config["pois"]
    crs = config["coordinates"]["projected_crs"]
    out = gdf.copy()
    out["scenario"] = "poi"

    new_poi = pois["new_poi"]
    near_mask = distance_m_to_point(out, new_poi["lon"], new_poi["lat"], crs) <= new_poi["radius_m"]
    selected = _sample_person_ids(out[near_mask], params["visit_ratio"], rng)

    visit_records = []
    dwell_minutes = int(params["dwell_minutes"])
    interval = int(params.get("dwell_interval_minutes", 1))
    for person_id in selected:
        person_near = out[(out["person_id"] == person_id) & near_mask].sort_values("time")
        if person_near.empty:
            continue
        base = person_near.iloc[0].copy()
        for minute in range(0, dwell_minutes + 1, interval):
            record = base.copy()
            record["time"] = base["time"] + pd.Timedelta(minutes=minute)
            record["lon"] = new_poi["lon"]
            record["lat"] = new_poi["lat"]
            record["mode"] = "walk"
            record["purpose"] = "visit_poi"
            record["geometry"] = gpd.points_from_xy([record["lon"]], [record["lat"]], crs=gdf.crs)[0]
            visit_records.append(record)

    visits = gpd.GeoDataFrame(visit_records, crs=gdf.crs) if visit_records else out.iloc[0:0].copy()
    return _combine_sort(out, visits)


def _station_to_campus_person_ids(gdf: gpd.GeoDataFrame, config: dict[str, Any]) -> list[str]:
    """Find people whose first point is nearer station and last point is nearer campus."""
    pois = config["pois"]
    crs = config["coordinates"]["projected_crs"]
    station_dist = distance_m_to_point(gdf, pois["station"]["lon"], pois["station"]["lat"], crs)
    campus_dist = distance_m_to_point(gdf, pois["campus"]["lon"], pois["campus"]["lat"], crs)

    temp = gdf[["person_id", "time"]].copy()
    temp["station_dist"] = station_dist.to_numpy()
    temp["campus_dist"] = campus_dist.to_numpy()

    eligible = []
    for person_id, group in temp.sort_values("time").groupby("person_id"):
        first = group.iloc[0]
        last = group.iloc[-1]
        if first["station_dist"] < first["campus_dist"] and last["campus_dist"] < last["station_dist"]:
            eligible.append(person_id)
    return eligible


def _sample_person_ids(df: gpd.GeoDataFrame, ratio: float, rng: np.random.Generator) -> list[str]:
    """Sample unique person IDs from a dataframe by ratio."""
    return _sample_ids(sorted(df["person_id"].dropna().unique().tolist()), ratio, rng)


def _sample_ids(ids: list[str], ratio: float, rng: np.random.Generator) -> list[str]:
    """Sample IDs using a deterministic generator."""
    if not ids or ratio <= 0:
        return []
    n = min(len(ids), max(1, int(round(len(ids) * float(ratio)))))
    return rng.choice(ids, size=n, replace=False).tolist()


def _duplicate_points(
    df: gpd.GeoDataFrame,
    ratio: float,
    duplicate_count: int,
    rng: np.random.Generator,
    minute_step: int,
) -> gpd.GeoDataFrame:
    """Duplicate sampled points and shift their timestamps to represent dwell."""
    if df.empty or ratio <= 0 or duplicate_count <= 0:
        return df.iloc[0:0].copy()

    sample_size = min(len(df), max(1, int(round(len(df) * float(ratio)))))
    sampled_idx = rng.choice(df.index.to_numpy(), size=sample_size, replace=False)
    records = []
    for _, row in df.loc[sampled_idx].iterrows():
        for copy_idx in range(duplicate_count):
            record = row.copy()
            record["time"] = row["time"] + pd.Timedelta(minutes=(copy_idx + 1) * minute_step)
            records.append(record)
    return gpd.GeoDataFrame(records, crs=df.crs) if records else df.iloc[0:0].copy()


def _combine_sort(base: gpd.GeoDataFrame, extra: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Combine base and extra records, then sort them by person and time."""
    combined = pd.concat([base, extra], ignore_index=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=base.crs).sort_values(["person_id", "time"]).reset_index(drop=True)
