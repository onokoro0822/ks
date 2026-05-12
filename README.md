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

## 入力CSV

最低限、以下に対応する列が必要です。実データの列名が異なる場合は `config.yaml` の `columns` を変更してください。

- person_id
- time
- lon
- lat
- mode
- purpose

## 主な出力

`data/output/` に以下を出力します。

- `baseline_points.csv`
- `rain_points.csv`
- `shuttle_points.csv`
- `poi_points.csv`
- `all_scenarios_points.csv`
- `*_trajectories.json`
- `all_scenarios_trajectories.json`
- `*_points.geojson`
- `*_metrics_*.csv`

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
