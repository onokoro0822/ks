from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd

from coordinates import distance_m_to_point


def build_metrics(gdf: gpd.GeoDataFrame, config: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Build summary metric tables for a scenario GeoDataFrame."""
    scenario = str(gdf["scenario"].iloc[0]) if "scenario" in gdf.columns and not gdf.empty else "unknown"
    time_bin = config["metrics"].get("time_bin", "1H")
    crs = config["coordinates"]["projected_crs"]
    pois = config["pois"]

    station_count = int(
        (distance_m_to_point(gdf, pois["station"]["lon"], pois["station"]["lat"], crs) <= pois["station"]["radius_m"]).sum()
    )
    campus_count = int(
        (distance_m_to_point(gdf, pois["campus"]["lon"], pois["campus"]["lat"], crs) <= pois["campus"]["radius_m"]).sum()
    )

    hourly = (
        gdf.assign(time_bin=gdf["time"].dt.floor(time_bin))
        .groupby("time_bin")["person_id"]
        .nunique()
        .reset_index(name="person_count")
    )
    hourly.insert(0, "scenario", scenario)

    mode = gdf.groupby("mode")["person_id"].nunique().reset_index(name="person_count")
    mode.insert(0, "scenario", scenario)

    summary = pd.DataFrame(
        [
            {
                "scenario": scenario,
                "total_points": len(gdf),
                "unique_persons": gdf["person_id"].nunique(),
                "station_dwell_points": station_count,
                "campus_dwell_points": campus_count,
            }
        ]
    )

    return {
        "hourly_persons": hourly,
        "mode_persons": mode,
        "summary": summary,
    }
