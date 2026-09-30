from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from coordinates import add_local_xy


UE_COLUMNS = ["scenario", "person_id", "time", "lon", "lat", "x", "y", "mode", "purpose"]


def scenario_output_dir(output_dir: Path, scenario_name: str) -> Path:
    """Return the output directory for a single scenario."""
    return output_dir / "scenarios" / scenario_name


def simulation_output_dir(output_dir: Path) -> Path:
    """Return the output directory for combined simulation results."""
    return output_dir / "simulation"


def prepare_for_export(gdf: gpd.GeoDataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Prepare a scenario dataframe with local x/y columns for Unreal Engine."""
    coord = config["coordinates"]
    out = add_local_xy(
        pd.DataFrame(gdf.drop(columns="geometry")),
        coord["origin_lon"],
        coord["origin_lat"],
        coord.get("projected_crs", "EPSG:6677"),
    )
    out = out.sort_values(["scenario", "person_id", "time"]).reset_index(drop=True)
    out["time"] = out["time"].dt.strftime(config["time"].get("output_format", "%Y-%m-%d %H:%M:%S"))
    return out[UE_COLUMNS]


def export_scenario(gdf: gpd.GeoDataFrame, scenario_name: str, output_dir: Path, config: dict[str, Any]) -> pd.DataFrame:
    """Export one scenario as CSV, GeoJSON, and trajectory JSON."""
    scenario_dir = scenario_output_dir(output_dir, scenario_name)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    csv_df = prepare_for_export(gdf, config)
    csv_df.to_csv(scenario_dir / "points.csv", index=False)
    export_trajectory_json(csv_df, scenario_dir / "trajectories.json")
    gdf.to_file(scenario_dir / "points.geojson", driver="GeoJSON")
    return csv_df


def export_metrics(metrics: dict[str, pd.DataFrame], scenario_name: str, output_dir: Path) -> None:
    """Export metric tables for one scenario."""
    scenario_dir = scenario_output_dir(output_dir, scenario_name)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    for metric_name, table in metrics.items():
        table.to_csv(scenario_dir / f"metrics_{metric_name}.csv", index=False)


def export_all_scenarios(csv_tables: list[pd.DataFrame], output_dir: Path) -> None:
    """Export combined CSV and trajectory JSON for all scenarios."""
    simulation_dir = simulation_output_dir(output_dir)
    simulation_dir.mkdir(parents=True, exist_ok=True)
    combined = pd.concat(csv_tables, ignore_index=True)
    combined.to_csv(simulation_dir / "all_scenarios_points.csv", index=False)
    export_trajectory_json(combined, simulation_dir / "all_scenarios_trajectories.json")


def export_trajectory_json(df: pd.DataFrame, output_path: Path) -> None:
    """Export records grouped by scenario and person_id for Unreal Engine animation."""
    payload = {
        "unit": "meter",
        "ue_scale_hint": "multiply x/y by 100 if your Unreal project uses centimeters",
        "scenarios": [],
    }

    sort_columns = ["scenario", "person_id", "time"]
    for scenario, scenario_df in df.sort_values(sort_columns).groupby("scenario", sort=False):
        people = []
        scenario_start = pd.to_datetime(scenario_df["time"]).min()

        for person_id, person_df in scenario_df.groupby("person_id", sort=False):
            points = []
            person_df = person_df.sort_values("time")
            for _, row in person_df.iterrows():
                row_time = pd.to_datetime(row["time"])
                points.append(
                    {
                        "t_seconds": float((row_time - scenario_start).total_seconds()),
                        "time": str(row["time"]),
                        "x": float(row["x"]),
                        "y": float(row["y"]),
                        "z": 0.0,
                        "lon": float(row["lon"]),
                        "lat": float(row["lat"]),
                        "mode": str(row["mode"]),
                        "purpose": str(row["purpose"]),
                    }
                )
            people.append({"person_id": str(person_id), "points": points})

        payload["scenarios"].append({"scenario": str(scenario), "people": people})

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
