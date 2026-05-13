# pflow_scenario

柏の葉キャンパス駅〜東京大学柏キャンパス周辺を対象に、擬似人流CSVを読み込み、ベースラインと簡易シナリオの比較データを出力する研究プロトタイプです。

## 機能

- 入力CSVの読み込みと列名マッピング
- lon/latからGeoDataFrameを作成
- bboxまたは中心点+半径による対象範囲抽出
- person_idごとの時系列整列
- ベースライン、雨天、シャトル導入、交流施設新設シナリオの作成
- Unreal Engine向けCSV出力
- GeoJSON出力
- 時間帯別、mode別、滞留点数などの集計CSV出力
- 実データがない場合のサンプルデータ生成

## セットアップ

```bash
cd pflow_scenario
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 実行

```bash
python src/main.py --config config.yaml
```

`generate_sample_if_missing: true` の場合、`input_csv` が存在しなければサンプルCSVを自動生成します。

## 可視化

出力済みの `all_scenarios_points.csv` から、シナリオ比較用のPNGを作成できます。

```bash
python src/visualize.py --input data/output/simulation/all_scenarios_points.csv --output-dir data/output/simulation
```

以下の画像が出力されます。

- `scenario_points_grid.png`
- `scenario_hourly_counts.png`

柏の地図上で点群を確認するWeb地図HTMLも作成できます。

```bash
python src/webmap.py --input data/output/simulation/all_scenarios_points.csv --output data/output/simulation/web_map.html
```

`web_map.html` をブラウザで開くと、OpenStreetMap上でシナリオ切り替え、時間スライダー、再生ができます。
人物ごとの前後点を線形補間して表示するため、観測点の時刻がそろっていなくても簡易的な移動アニメーションとして確認できます。

## 入力CSV

最低限、以下に対応する列が必要です。実データの列名が異なる場合は `config.yaml` の `columns` を変更してください。

- person_id
- time
- lon
- lat
- mode
- purpose

PFLOW/People Flow Projectの実データを使う場合は、JoRAS等の正規手続きで取得したCSVを `data/input/` に置き、`config.yaml` の `input_csv` をそのファイルに変更してください。

ヘッダー付きCSVの場合:

```yaml
csv:
  encoding: utf-8
  has_header: true
columns:
  person_id: person_id
  time: time
  lon: lon
  lat: lat
  mode: mode
  purpose: purpose
```

ヘッダーなしCSVの場合は、0始まりの列番号で指定できます。実際の列順は取得したデータ仕様書に合わせて調整してください。

```yaml
generate_sample_if_missing: false
input_csv: data/input/your_pflow_data.csv
csv:
  encoding: utf-8
  has_header: false
columns:
  person_id: 0
  mode: 1
  purpose: 2
  time: 3
  lon: 4
  lat: 5
```

PFLOWのWebAPIは、時空間内挿・最近傍道路点・経路探索などの補助処理に使えます。このプロトタイプ本体は、まず取得済みCSVをシナリオ加工・可視化用データへ変換する構成です。

## 主な出力

`data/output/` に以下を出力します。

- `scenarios/baseline/points.csv`
- `scenarios/baseline/points.geojson`
- `scenarios/baseline/trajectories.json`
- `scenarios/baseline/metrics_*.csv`
- `scenarios/rain/`
- `scenarios/shuttle/`
- `scenarios/poi/`
- `simulation/all_scenarios_points.csv`
- `simulation/all_scenarios_trajectories.json`
- `simulation/scenario_points_grid.png`
- `simulation/scenario_hourly_counts.png`
- `simulation/web_map.html`

最終CSVには以下の列を含みます。

- scenario
- person_id
- time
- lon
- lat
- x
- y
- mode
- purpose

`x`, `y` は `coordinates.origin_lon`, `coordinates.origin_lat` を原点とするメートル単位のローカル座標です。

## Unreal Engine向け軌跡JSON

`*_trajectories.json` は、シナリオごと、人物ごとの軌跡に変換したJSONです。各点には `t_seconds`, `x`, `y`, `z`, `mode`, `purpose` が含まれます。

Unreal Engineの標準単位はcmなので、必要に応じて `x` と `y` に100を掛けて配置してください。
