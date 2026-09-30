#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
国土交通省
『大規模開発地区関連交通計画マニュアル 改訂版（平成26年6月）』
https://www.mlit.go.jp/toshi/city_plan/content/001480895.pdf

に基づく、商業施設・オフィス・ホテルの
発生集中交通量（人TE/日）を対話形式で推定するスクリプト。

方針
----
- 商業施設の地域区分は a / b / c で直接選択する。
- オフィスの地域区分も a / b で直接選択する。
- 昼間人口密度・第3次産業従業人口密度は入力しない。
  （本来は地域区分を判断する根拠だが、このスクリプトでは区分を直接選ぶ）
- 発生集中原単位は100人TE/ha・日単位で切り捨てる。
- ホテルの1,300人TE/ha・日はマニュアル上の「参考値」。
"""

from dataclasses import dataclass
import csv
import math
import sys


# =========================================================
# 結果を保持するデータクラス
# =========================================================

@dataclass
class FacilityResult:
    name: str
    area_m2: float
    area_ha: float
    category: str
    base_rate: float
    alpha1: float
    alpha2: float
    raw_rate: float
    applied_rate: float
    trip_ends: float


# =========================================================
# 共通関数
# =========================================================

def ask_float(prompt, default=None, min_value=None, max_value=None):
    """数値を対話形式で入力する。Enterのみならdefaultを使用。"""
    while True:
        suffix = f" [{default:g}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()

        if raw == "" and default is not None:
            value = float(default)
        else:
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                print("数値で入力してください。")
                continue

        if min_value is not None and value < min_value:
            print(f"{min_value}以上で入力してください。")
            continue

        if max_value is not None and value > max_value:
            print(f"{max_value}以下で入力してください。")
            continue

        return value


def ask_symbol_choice(prompt, choices, default=None):
    """
    a / b / c などの記号で選択する。

    choices例:
    {
        "a": "三大都市圏中心部",
        "b": "三大都市圏郊外部および地方中枢都市",
        "c": "三大都市圏周辺部および地方都市",
    }
    """
    print(prompt)

    for key, label in choices.items():
        print(f"  {key} : {label}")

    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"選択{suffix}: ").strip().lower()

        if raw == "" and default is not None:
            raw = default

        if raw in choices:
            return raw

        print(
            f"{', '.join(choices.keys())} のいずれかを入力してください。"
        )


def ask_yes_no(prompt, default=True):
    """y / n を入力する。"""
    suffix = "Y/n" if default else "y/N"

    while True:
        raw = input(f"{prompt} [{suffix}]: ").strip().lower()

        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False

        print("y または n を入力してください。")


def linear(x, x1, y1, x2, y2):
    """2点 (x1, y1), (x2, y2) の間を線形補間する。"""
    return y1 + (y2 - y1) * (x - x1) / (x2 - x1)


def floor100(value):
    """100人TE/ha・日単位で切り捨てる。"""
    return math.floor(value / 100.0) * 100.0


# =========================================================
# 商業施設
# =========================================================

COMMERCIAL_AREA_TYPES = {
    "a": "三大都市圏中心部",
    "b": "三大都市圏郊外部および地方中枢都市",
    "c": "三大都市圏周辺部および地方都市",
}

DAY_TYPES = {
    "a": "平日",
    "b": "休日",
}

# マニュアル p.17-20 相当
COMMERCIAL_BASE_RATES = {
    "平日": {
        "a": 20600.0,
        "b": 11600.0,
        "c": 10600.0,
    },
    "休日": {
        "a": 21800.0,
        "b": 18600.0,
        "c": 16100.0,
    },
}


def commercial_alpha_area(area_ha, area_type, day):
    """
    商業施設の延床面積による割引率 alpha1。

    a: 三大都市圏中心部
       マニュアル上、割引率を設定しない → 1.0

    b: 三大都市圏郊外部および地方中枢都市
       平日: 1.5haまでは1.0、5.5haで0.8、その後0.8
       休日: 1.5haまでは1.0、4.5haで0.7、その後0.7

    c: 三大都市圏周辺部および地方都市
       平日: 1.5haまでは1.0、7.0haで0.7、その後0.7
       休日: 1.5haまでは1.0、6.0haで0.85、その後0.85
    """

    if area_type == "a":
        return 1.0

    if area_type == "b":
        if day == "平日":
            if area_ha <= 1.5:
                return 1.0
            if area_ha < 5.5:
                return linear(area_ha, 1.5, 1.0, 5.5, 0.8)
            return 0.8

        # 休日
        if area_ha <= 1.5:
            return 1.0
        if area_ha < 4.5:
            return linear(area_ha, 1.5, 1.0, 4.5, 0.7)
        return 0.7

    # area_type == "c"
    if day == "平日":
        if area_ha <= 1.5:
            return 1.0
        if area_ha < 7.0:
            return linear(area_ha, 1.5, 1.0, 7.0, 0.7)
        return 0.7

    # 休日
    if area_ha <= 1.5:
        return 1.0
    if area_ha < 6.0:
        return linear(area_ha, 1.5, 1.0, 6.0, 0.85)
    return 0.85


def commercial_alpha_station(distance_m, area_type, day):
    """
    商業施設の鉄道駅からの距離による割引率 alpha2。

    - 三大都市圏中心部: 1.0
    - 休日: 駅距離による割引なし → 1.0
    - b 平日: 150mまでは1.0、750mで0.9、その後0.9
    - c 平日: 150mまでは1.0、700mで0.95、その後0.95
    """

    if area_type == "a" or day == "休日":
        return 1.0

    if area_type == "b":
        if distance_m <= 150:
            return 1.0
        if distance_m < 750:
            return linear(distance_m, 150, 1.0, 750, 0.9)
        return 0.9

    # area_type == "c"
    if distance_m <= 150:
        return 1.0
    if distance_m < 700:
        return linear(distance_m, 150, 1.0, 700, 0.95)
    return 0.95


def calculate_commercial(
    area_m2,
    area_type,
    day,
    station_distance_m,
):
    """商業施設の発生集中交通量を計算する。"""

    area_ha = area_m2 / 10000.0
    base = COMMERCIAL_BASE_RATES[day][area_type]

    alpha1 = commercial_alpha_area(
        area_ha,
        area_type,
        day,
    )

    alpha2 = commercial_alpha_station(
        station_distance_m,
        area_type,
        day,
    )

    raw = base * alpha1 * alpha2
    applied = floor100(raw)
    trip_ends = area_ha * applied

    return FacilityResult(
        name="商業施設",
        area_m2=area_m2,
        area_ha=area_ha,
        category=f"{COMMERCIAL_AREA_TYPES[area_type]}・{day}",
        base_rate=base,
        alpha1=alpha1,
        alpha2=alpha2,
        raw_rate=raw,
        applied_rate=applied,
        trip_ends=trip_ends,
    )


# =========================================================
# オフィス
# =========================================================

OFFICE_LOCATION_TYPES = {
    "a": "都心部",
    "b": "周辺部",
}

OFFICE_BUILDING_TYPES = {
    "a": "一般事務所ビル",
    "b": "単館型事務所ビル",
}

# マニュアル p.13-16 相当
OFFICE_BASE_RATES = {
    ("都心部", "一般事務所ビル"): 3800.0,
    ("周辺部", "一般事務所ビル"): 3300.0,
    ("都心部", "単館型事務所ビル"): 3100.0,
    ("周辺部", "単館型事務所ビル"): 2200.0,
}

# 商業床面積率10%超15%以下の場合に使用する
# 図III-4の a, b
OFFICE_AB = {
    ("都心部", "一般事務所ビル"): (4300.0, 3800.0),
    ("都心部", "単館型事務所ビル"): (4100.0, 3100.0),
    ("周辺部", "一般事務所ビル"): (4100.0, 3300.0),
    ("周辺部", "単館型事務所ビル"): (3500.0, 2200.0),
}


def office_alpha_commercial_floor(commercial_floor_ratio):
    """
    事務所の商業床面積率による割引率 alpha1。

    0%   → 0.75
    5%   → 1.00
    5-10% → 1.00
    """

    if commercial_floor_ratio <= 0:
        return 0.75

    if commercial_floor_ratio < 5:
        return linear(
            commercial_floor_ratio,
            0,
            0.75,
            5,
            1.0,
        )

    return 1.0


def office_alpha_station(distance_m):
    """
    事務所の鉄道駅からの距離による割引率 alpha2。

    150mまでは1.0
    150-500mで1.0 → 0.7
    500m以上は0.7
    """

    if distance_m <= 150:
        return 1.0

    if distance_m < 500:
        return linear(
            distance_m,
            150,
            1.0,
            500,
            0.7,
        )

    return 0.7


def calculate_office(
    area_m2,
    location_key,
    building_key,
    commercial_floor_ratio,
    station_distance_m,
):
    """オフィスの発生集中交通量を計算する。"""

    area_ha = area_m2 / 10000.0

    location = OFFICE_LOCATION_TYPES[location_key]
    building_type = OFFICE_BUILDING_TYPES[building_key]

    base = OFFICE_BASE_RATES[
        (location, building_type)
    ]

    alpha2 = office_alpha_station(
        station_distance_m
    )

    category = f"{location}・{building_type}"

    # ① 商業床面積率10%以下
    if commercial_floor_ratio <= 10:
        alpha1 = office_alpha_commercial_floor(
            commercial_floor_ratio
        )

        raw = base * alpha1 * alpha2

    # ② 商業床面積率10%超15%以下
    elif commercial_floor_ratio <= 15:
        a_value, b_value = OFFICE_AB[
            (location, building_type)
        ]

        # 10%でb、15%でaとなる線形補間
        base = linear(
            commercial_floor_ratio,
            10,
            b_value,
            15,
            a_value,
        )

        alpha1 = 1.0
        raw = base * alpha2

        category += "・商業床10%超15%以下"

    # ③ 商業床面積率15%超
    else:
        # マニュアルでは事務所と商業施設を用途別に算定する。
        # ここでは入力された「オフィス延床面積」に対する
        # 事務所部分の計算のみ行い、商業部分は別途商業施設として
        # 入力する前提とする。
        alpha1 = office_alpha_commercial_floor(0)

        raw = base * alpha1 * alpha2

        category += "・商業床15%超（事務所部分のみ計算）"

    applied = floor100(raw)
    trip_ends = area_ha * applied

    return FacilityResult(
        name="オフィス",
        area_m2=area_m2,
        area_ha=area_ha,
        category=category,
        base_rate=base,
        alpha1=alpha1,
        alpha2=alpha2,
        raw_rate=raw,
        applied_rate=applied,
        trip_ends=trip_ends,
    )


# =========================================================
# ホテル
# =========================================================

def calculate_hotel(area_m2):
    """
    ホテルはマニュアルの参考値
    平均 1,300人TE/ha・日を使用する。
    """

    area_ha = area_m2 / 10000.0

    base = 1300.0
    alpha1 = 1.0
    alpha2 = 1.0

    raw = base * alpha1 * alpha2
    applied = floor100(raw)
    trip_ends = area_ha * applied

    return FacilityResult(
        name="ホテル",
        area_m2=area_m2,
        area_ha=area_ha,
        category="ホテル参考値",
        base_rate=base,
        alpha1=alpha1,
        alpha2=alpha2,
        raw_rate=raw,
        applied_rate=applied,
        trip_ends=trip_ends,
    )


# =========================================================
# 表示・CSV
# =========================================================

def print_result(result):
    print("\n" + "-" * 70)
    print(result.name)
    print("-" * 70)

    print(f"区分             : {result.category}")
    print(
        f"延床面積         : "
        f"{result.area_m2:,.0f} m2 "
        f"= {result.area_ha:.2f} ha"
    )
    print(
        f"基準原単位       : "
        f"{result.base_rate:,.0f} 人TE/ha・日"
    )
    print(f"alpha1           : {result.alpha1:.4f}")
    print(f"alpha2           : {result.alpha2:.4f}")
    print(
        f"原単位（丸め前） : "
        f"{result.raw_rate:,.1f} 人TE/ha・日"
    )
    print(
        f"適用原単位       : "
        f"{result.applied_rate:,.0f} 人TE/ha・日"
    )
    print(
        f"発生集中交通量   : "
        f"{result.trip_ends:,.0f} 人TE/日"
    )


def save_csv(
    results,
    total,
    adjusted_total=None,
    path="facility_trip_estimation_result.csv",
):

    with open(
        path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:
        writer = csv.writer(f)

        writer.writerow([
            "用途",
            "延床面積_m2",
            "延床面積_ha",
            "区分",
            "基準原単位_人TE_ha日",
            "alpha1",
            "alpha2",
            "原単位_丸め前",
            "適用原単位_人TE_ha日",
            "発生集中交通量_人TE日",
        ])

        for r in results:
            writer.writerow([
                r.name,
                r.area_m2,
                r.area_ha,
                r.category,
                r.base_rate,
                r.alpha1,
                r.alpha2,
                r.raw_rate,
                r.applied_rate,
                r.trip_ends,
            ])

        writer.writerow([])
        writer.writerow([
            "単純合計_人TE日",
            total,
        ])

        if adjusted_total is not None:
            writer.writerow([
                "内々交通低減後合計_人TE日",
                adjusted_total,
            ])

    return path


# =========================================================
# main
# =========================================================

def run_manual_dialogue():
    print("=" * 72)
    print("国交省マニュアル準拠 発生集中交通量計算")
    print("=" * 72)
    print(
        "地域区分は人口密度から自動判定せず、"
        "記号で直接選択します。"
    )

    # -----------------------------------------------------
    # 共通条件
    # -----------------------------------------------------

    station_distance = ask_float(
        "\n最寄り鉄道駅からの距離 [m]",
        default=100,
        min_value=0,
    )

    # -----------------------------------------------------
    # 商業施設
    # -----------------------------------------------------

    print("\n" + "=" * 72)
    print("[商業施設]")
    print("=" * 72)

    commercial_area = ask_float(
        "延床面積 [m2]",
        default=60000,
        min_value=0,
    )

    commercial_area_type = ask_symbol_choice(
        "地域区分を選択してください。",
        COMMERCIAL_AREA_TYPES,
        default="b",
    )

    day_key = ask_symbol_choice(
        "対象日を選択してください。",
        DAY_TYPES,
        default="a",
    )

    day = DAY_TYPES[day_key]

    commercial = calculate_commercial(
        area_m2=commercial_area,
        area_type=commercial_area_type,
        day=day,
        station_distance_m=station_distance,
    )

    # -----------------------------------------------------
    # オフィス
    # -----------------------------------------------------

    print("\n" + "=" * 72)
    print("[オフィス]")
    print("=" * 72)

    office_area = ask_float(
        "延床面積 [m2]",
        default=60000,
        min_value=0,
    )

    office_location = ask_symbol_choice(
        "地域区分を選択してください。",
        OFFICE_LOCATION_TYPES,
        default="a",
    )

    office_building_type = ask_symbol_choice(
        "事務所ビルのタイプを選択してください。",
        OFFICE_BUILDING_TYPES,
        default="a",
    )

    commercial_floor_ratio = ask_float(
        "事務所建物内の商業床面積率 [%]",
        default=0,
        min_value=0,
        max_value=100,
    )

    office = calculate_office(
        area_m2=office_area,
        location_key=office_location,
        building_key=office_building_type,
        commercial_floor_ratio=commercial_floor_ratio,
        station_distance_m=station_distance,
    )

    # -----------------------------------------------------
    # ホテル
    # -----------------------------------------------------

    print("\n" + "=" * 72)
    print("[ホテル]")
    print("=" * 72)

    hotel_area = ask_float(
        "延床面積 [m2]",
        default=30000,
        min_value=0,
    )

    hotel = calculate_hotel(
        area_m2=hotel_area
    )

    # -----------------------------------------------------
    # 結果
    # -----------------------------------------------------

    results = [
        commercial,
        office,
        hotel,
    ]

    print("\n" + "=" * 72)
    print("計算結果")
    print("=" * 72)

    for result in results:
        print_result(result)

    total = sum(
        result.trip_ends
        for result in results
    )

    print("\n" + "=" * 72)
    print(f"単純合計 : {total:,.0f} 人TE/日")

    adjusted_total = None

    # マニュアルでは商業床15%超の事務所＋商業複合開発について
    # 内々交通低減率の参考値として概ね5%を示している。
    if ask_yes_no(
        "\n商業＋オフィスの内々交通5%低減を参考表示しますか？",
        default=True,
    ):
        office_commercial_total = (
            commercial.trip_ends
            + office.trip_ends
        )

        reduction = (
            office_commercial_total
            * 0.05
        )

        adjusted_total = (
            total
            - reduction
        )

        print(
            f"内々交通低減量 : "
            f"{reduction:,.0f} 人TE/日"
        )

        print(
            f"低減後合計     : "
            f"{adjusted_total:,.0f} 人TE/日"
        )

    print("\n注意")
    print(
        "- TEは発生＋集中のトリップエンド数であり、"
        "ユニーク来訪者数ではありません。"
    )
    print(
        "- ホテルの1,300人TE/ha・日は"
        "マニュアル上の参考値（標準偏差600）です。"
    )
    print(
        "- 商業床面積率15%超の事務所は、"
        "事務所部分と商業部分を用途別に算定するのが原則です。"
    )

    if ask_yes_no(
        "\n結果をCSV保存しますか？",
        default=True,
    ):
        path = save_csv(
            results,
            total,
            adjusted_total,
        )

        print(f"保存しました: {path}")


def main():
    """LLM対話を既定にし、--manualで従来の固定質問へ戻せるようにする。"""
    from llm_facility_dialogue import run_cli

    run_cli(
        calculations=sys.modules[__name__],
        manual_callback=run_manual_dialogue,
    )


if __name__ == "__main__":
    main()
