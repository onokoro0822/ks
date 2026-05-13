from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


REQUIRED_COLUMNS = {"scenario", "person_id", "time", "x", "y", "mode", "purpose"}
MODE_COLORS = {
    "walk": "#2f7d32",
    "bus": "#1976d2",
    "bike": "#f57c00",
    "shuttle": "#8e24aa",
}


def load_points(csv_path: Path) -> pd.DataFrame:
    """Load an exported scenario CSV and validate columns for plotting."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns for visualization: {missing}")

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    if df["time"].isna().any():
        raise ValueError("Failed to parse some time values in the CSV.")

    return df


def plot_scenario_grid(df: pd.DataFrame, output_path: Path) -> None:
    """Create a 2x2 scatter plot comparing scenarios in local meter coordinates."""
    scenarios = ["baseline", "rain", "shuttle", "poi"]
    available = [scenario for scenario in scenarios if scenario in set(df["scenario"])]
    if not available:
        raise ValueError("No known scenarios found. Expected one of baseline/rain/shuttle/poi.")

    fig, axes = plt.subplots(2, 2, figsize=(12, 10), sharex=True, sharey=True)
    axes_flat = axes.ravel()

    x_min, x_max = df["x"].min(), df["x"].max()
    y_min, y_max = df["y"].min(), df["y"].max()
    x_pad = max((x_max - x_min) * 0.08, 20)
    y_pad = max((y_max - y_min) * 0.08, 20)

    for ax, scenario in zip(axes_flat, scenarios):
        scenario_df = df[df["scenario"] == scenario]
        if scenario_df.empty:
            ax.set_visible(False)
            continue

        for mode, mode_df in scenario_df.groupby("mode"):
            ax.scatter(
                mode_df["x"],
                mode_df["y"],
                s=8,
                alpha=0.45,
                label=mode,
                color=MODE_COLORS.get(mode, "#616161"),
                edgecolors="none",
            )

        ax.set_title(f"{scenario} ({len(scenario_df):,} points)")
        ax.set_xlim(x_min - x_pad, x_max + x_pad)
        ax.set_ylim(y_min - y_pad, y_max + y_pad)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.6)
        ax.set_xlabel("x from Kashiwanoha-campus Station (m)")
        ax.set_ylabel("y from Kashiwanoha-campus Station (m)")

    handles, labels = axes_flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=max(1, len(labels)))
    fig.suptitle("Person-flow Scenario Comparison", fontsize=16)
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_hourly_counts(df: pd.DataFrame, output_path: Path) -> None:
    """Create a line chart of unique persons per hour for each scenario."""
    hourly = (
        df.assign(time_bin=df["time"].dt.floor("1h"))
        .groupby(["scenario", "time_bin"])["person_id"]
        .nunique()
        .reset_index(name="person_count")
    )

    fig, ax = plt.subplots(figsize=(11, 5))
    for scenario, group in hourly.groupby("scenario"):
        ax.plot(group["time_bin"], group["person_count"], marker="o", linewidth=2, label=scenario)

    ax.set_title("Hourly Unique Persons by Scenario")
    ax.set_xlabel("time")
    ax.set_ylabel("unique persons")
    ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.6)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def run(input_csv: Path, output_dir: Path) -> None:
    """Generate visualization PNG files from exported scenario CSV."""
    df = load_points(input_csv)
    plot_scenario_grid(df, output_dir / "scenario_points_grid.png")
    plot_hourly_counts(df, output_dir / "scenario_hourly_counts.png")
    print(f"Saved visualizations to: {output_dir}")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Visualize exported person-flow scenario CSV files.")
    parser.add_argument("--input", default="data/output/all_scenarios_points.csv", help="Path to exported points CSV")
    parser.add_argument("--output-dir", default="data/output", help="Directory for PNG outputs")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(Path(args.input), Path(args.output_dir))
