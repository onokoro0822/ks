from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {"scenario", "person_id", "time", "lon", "lat", "mode", "purpose"}
MODE_COLORS = {
    "walk": "#2f7d32",
    "bus": "#1976d2",
    "bike": "#f57c00",
    "shuttle": "#8e24aa",
}


def load_points(csv_path: Path) -> pd.DataFrame:
    """Load exported scenario points for web map visualization."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns for web map: {missing}")

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    if df["time"].isna().any():
        raise ValueError("Failed to parse some time values in the CSV.")

    return df.sort_values(["scenario", "time", "person_id"]).reset_index(drop=True)


def build_payload(df: pd.DataFrame) -> dict:
    """Convert dataframe rows into compact JSON for the browser."""
    time_values = sorted(df["time"].dt.strftime("%Y-%m-%d %H:%M:%S").unique().tolist())
    records = []
    for _, row in df.iterrows():
        records.append(
            {
                "scenario": str(row["scenario"]),
                "person_id": str(row["person_id"]),
                "time": row["time"].strftime("%Y-%m-%d %H:%M:%S"),
                "lon": float(row["lon"]),
                "lat": float(row["lat"]),
                "mode": str(row["mode"]),
                "purpose": str(row["purpose"]),
            }
        )

    return {
        "center": {
            "lat": float(df["lat"].mean()),
            "lon": float(df["lon"].mean()),
        },
        "scenarios": sorted(df["scenario"].unique().tolist()),
        "times": time_values,
        "records": records,
        "modeColors": MODE_COLORS,
    }


def write_html(payload: dict, output_path: Path) -> None:
    """Write a standalone Leaflet web map HTML file."""
    payload_json = json.dumps(payload, ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Person-flow Scenario Web Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body {{
      height: 100%;
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    #map {{
      height: 100%;
      width: 100%;
    }}
    .panel {{
      position: absolute;
      z-index: 1000;
      top: 16px;
      left: 16px;
      width: min(420px, calc(100vw - 32px));
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid #d0d7de;
      border-radius: 8px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.16);
      padding: 12px;
    }}
    .row {{
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 8px;
    }}
    label {{
      font-size: 13px;
      font-weight: 600;
      color: #24292f;
    }}
    select, input[type="range"], button {{
      font: inherit;
    }}
    select {{
      flex: 1;
      padding: 5px 8px;
    }}
    input[type="range"] {{
      flex: 1;
    }}
    button {{
      min-width: 72px;
      padding: 6px 10px;
      border: 1px solid #8c959f;
      border-radius: 6px;
      background: #f6f8fa;
      cursor: pointer;
    }}
    .time {{
      font-size: 13px;
      color: #57606a;
      min-width: 150px;
      text-align: right;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      margin-top: 10px;
      font-size: 12px;
      color: #57606a;
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 5px;
    }}
    .swatch {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }}
    .count {{
      margin-top: 8px;
      font-size: 12px;
      color: #57606a;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <label for="scenario">Scenario</label>
    <div class="row">
      <select id="scenario"></select>
      <button id="play">Play</button>
    </div>
    <div class="row">
      <input id="timeSlider" type="range" min="0" value="0">
      <div id="timeLabel" class="time"></div>
    </div>
    <div id="legend" class="legend"></div>
    <div id="count" class="count"></div>
  </div>
  <script>
    const payload = {payload_json};
    const map = L.map("map").setView([payload.center.lat, payload.center.lon], 14);
    L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors"
    }}).addTo(map);

    const layer = L.layerGroup().addTo(map);
    const scenarioSelect = document.getElementById("scenario");
    const timeSlider = document.getElementById("timeSlider");
    const timeLabel = document.getElementById("timeLabel");
    const playButton = document.getElementById("play");
    const countLabel = document.getElementById("count");
    const legend = document.getElementById("legend");
    let timer = null;

    for (const scenario of payload.scenarios) {{
      const option = document.createElement("option");
      option.value = scenario;
      option.textContent = scenario;
      scenarioSelect.appendChild(option);
    }}

    timeSlider.max = Math.max(payload.times.length - 1, 0);

    for (const [mode, color] of Object.entries(payload.modeColors)) {{
      const item = document.createElement("div");
      item.className = "legend-item";
      item.innerHTML = `<span class="swatch" style="background:${{color}}"></span>${{mode}}`;
      legend.appendChild(item);
    }}

    function pointColor(mode) {{
      return payload.modeColors[mode] || "#616161";
    }}

    function render() {{
      const scenario = scenarioSelect.value;
      const time = payload.times[Number(timeSlider.value)];
      timeLabel.textContent = time || "";
      layer.clearLayers();

      const activeRecords = payload.records.filter((record) => (
        record.scenario === scenario && record.time === time
      ));

      for (const record of activeRecords) {{
        const marker = L.circleMarker([record.lat, record.lon], {{
          radius: 5,
          color: pointColor(record.mode),
          fillColor: pointColor(record.mode),
          fillOpacity: 0.72,
          weight: 1
        }});
        marker.bindPopup(
          `<b>${{record.person_id}}</b><br>` +
          `${{record.time}}<br>` +
          `mode: ${{record.mode}}<br>` +
          `purpose: ${{record.purpose}}`
        );
        marker.addTo(layer);
      }}

      countLabel.textContent = `${{scenario}} / visible points: ${{activeRecords.length}}`;
    }}

    function stop() {{
      if (timer !== null) {{
        clearInterval(timer);
        timer = null;
      }}
      playButton.textContent = "Play";
    }}

    function play() {{
      if (timer !== null) {{
        stop();
        return;
      }}
      playButton.textContent = "Pause";
      timer = setInterval(() => {{
        const next = Number(timeSlider.value) + 1;
        timeSlider.value = next > Number(timeSlider.max) ? 0 : next;
        render();
      }}, 700);
    }}

    scenarioSelect.addEventListener("change", render);
    timeSlider.addEventListener("input", render);
    playButton.addEventListener("click", play);
    render();
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def run(input_csv: Path, output_html: Path) -> None:
    """Generate an interactive web map HTML from exported scenario points."""
    df = load_points(input_csv)
    write_html(build_payload(df), output_html)
    print(f"Saved web map to: {output_html}")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Generate an interactive Leaflet web map.")
    parser.add_argument(
        "--input",
        default="data/output/simulation/all_scenarios_points.csv",
        help="Path to exported points CSV",
    )
    parser.add_argument("--output", default="data/output/simulation/web_map.html", help="Output HTML path")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(Path(args.input), Path(args.output))
