from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from coordinates import make_geodataframe
from export import export_all_scenarios, export_metrics, export_scenario
from load_data import generate_sample_data, load_csv, resolve_path, sort_by_person_time
from metrics import build_metrics
from scenarios import apply_poi_scenario, apply_rain_scenario, apply_shuttle_scenario, baseline
from spatial_filter import filter_area


def load_config(config_path: Path) -> dict[str, Any]:
    """Load YAML configuration."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(config_path: Path) -> None:
    """Run the full person-flow scenario pipeline."""
    project_root = config_path.resolve().parent
    config = load_config(config_path)
    rng = np.random.default_rng(config.get("random_seed", 42))

    input_csv = resolve_path(project_root, config["input_csv"])
    output_dir = resolve_path(project_root, config["output_dir"])

    if not input_csv.exists() and config.get("generate_sample_if_missing", True):
        print(f"Input CSV not found. Generating sample data: {input_csv}")
        generate_sample_data(config, input_csv)

    df = load_csv(input_csv, config["columns"], config["time"].get("format"))
    df = sort_by_person_time(df)
    gdf = make_geodataframe(df)
    gdf = filter_area(gdf, config["area"], config["coordinates"].get("projected_crs", "EPSG:6677"))
    if gdf.empty:
        raise ValueError("No records remain after spatial filtering. Check area settings.")

    scenario_gdfs = {
        "baseline": baseline(gdf),
        "rain": apply_rain_scenario(gdf, config, rng),
        "shuttle": apply_shuttle_scenario(gdf, config, rng),
        "poi": apply_poi_scenario(gdf, config, rng),
    }

    csv_tables = []
    for scenario_name, scenario_gdf in scenario_gdfs.items():
        csv_table = export_scenario(scenario_gdf, scenario_name, output_dir, config)
        export_metrics(build_metrics(scenario_gdf, config), scenario_name, output_dir)
        csv_tables.append(csv_table)
        print(f"Exported {scenario_name}: {len(scenario_gdf)} points")

    export_all_scenarios(csv_tables, output_dir)
    print(f"Done. Outputs are in: {output_dir}")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Generate baseline and scenario person-flow datasets.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(Path(args.config))
