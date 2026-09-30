#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""施設別の到着年齢・性別分布を発生集中交通量へ適用する。

年齢・性別構成は ``chukyo_pt_2022_age_gender_distribution_reference.json``
に保存された、第6回中京都市圏PT調査の施設用途別到着トリップ構成比を使う。

発生集中交通量（人TE/日）は発生と集中の合計であるため、来訪実人数には直接変換しない。
本モジュールでは日単位で発生と集中が均衡すると仮定し、``人TE / 2`` を到着人トリップ数の
近似として年齢・性別の同時分布を適用する。結果はユニーク来訪者数ではない。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Hashable, Iterable


REFERENCE_PATH = Path(__file__).with_name(
    "chukyo_pt_2022_age_gender_distribution_reference.json"
)

AGE_GROUPS = (
    "10歳未満",
    "10～19歳",
    "20～29歳",
    "30～39歳",
    "40～49歳",
    "50～59歳",
    "60～69歳",
    "70～79歳",
    "80歳以上",
)

GENDER_KEYS = ("male", "female", "other")
GENDER_LABELS = {
    "male": "男性",
    "female": "女性",
    "other": "その他・不詳",
}
GENDER_JSON_FIELDS = {
    "male": "male_pct",
    "female": "female_pct",
    "other": "other_pct",
}

FACILITY_NAMES = {
    "commercial": "商業施設",
    "office": "オフィス",
    "hotel": "ホテル",
}

RESULT_NAME_TO_KEY = {
    "商業施設": "commercial",
    "オフィス": "office",
    "ホテル": "hotel",
}


def load_distribution_reference(path: Path = REFERENCE_PATH) -> dict[str, Any]:
    """年齢・性別構成比JSONを読み、構造と合計を検査する。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"年齢・性別分布JSONが見つかりません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"年齢・性別分布JSONの形式が不正です: {exc}") from exc

    if payload.get("unit") != "percent":
        raise ValueError("年齢・性別分布JSONのunitはpercentである必要があります。")
    facilities = payload.get("facilities")
    if not isinstance(facilities, dict):
        raise ValueError("年齢・性別分布JSONにfacilitiesがありません。")

    for facility_name in FACILITY_NAMES.values():
        ages = facilities.get(facility_name)
        if not isinstance(ages, dict):
            raise ValueError(f"{facility_name}の年齢・性別分布がありません。")
        joint_total = 0.0
        for age in AGE_GROUPS:
            values = ages.get(age)
            if not isinstance(values, dict):
                raise ValueError(f"{facility_name}・{age}の分布がありません。")
            gender_total = 0.0
            for field in GENDER_JSON_FIELDS.values():
                value = values.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{facility_name}・{age}・{field}が数値ではありません。")
                if value < 0:
                    raise ValueError(f"{facility_name}・{age}・{field}が負です。")
                gender_total += float(value)
            age_total = values.get("age_total_pct")
            if isinstance(age_total, bool) or not isinstance(age_total, (int, float)):
                raise ValueError(f"{facility_name}・{age}・age_total_pctが数値ではありません。")
            if not math.isclose(gender_total, float(age_total), abs_tol=0.0002):
                raise ValueError(f"{facility_name}・{age}の性別合計と年齢合計が一致しません。")
            joint_total += gender_total
        if not math.isclose(joint_total, 100.0, abs_tol=0.001):
            raise ValueError(f"{facility_name}の年齢・性別構成比合計が100%ではありません。")
    return payload


def joint_profile_percentages(facility_key: str) -> dict[tuple[str, str], float]:
    """施設の年齢×性別同時構成比を0〜1へ正規化して返す。"""
    facility_name = FACILITY_NAMES[facility_key]
    ages = load_distribution_reference()["facilities"][facility_name]
    raw = {
        (age, gender): float(ages[age][GENDER_JSON_FIELDS[gender]]) / 100.0
        for age in AGE_GROUPS
        for gender in GENDER_KEYS
    }
    total = sum(raw.values())
    if total <= 0:
        raise ValueError("年齢・性別分布の合計が0です。")
    return {key: value / total for key, value in raw.items()}


def profile_percentages(facility_key: str) -> dict[str, float]:
    """年齢×性別同時構成比を性別方向に合計し、年齢構成比を返す。"""
    joint = joint_profile_percentages(facility_key)
    return {
        age: sum(joint[(age, gender)] for gender in GENDER_KEYS)
        for age in AGE_GROUPS
    }


def allocate_integer(
    total: int,
    weights: dict[Hashable, float],
    keys: Iterable[Hashable] | None = None,
) -> dict[Hashable, int]:
    """最大剰余法で、合計を保ったまま任意の区分へ整数配分する。"""
    if total < 0:
        raise ValueError("配分人数は0以上である必要があります。")
    ordered_keys = tuple(keys) if keys is not None else tuple(weights)
    if not ordered_keys:
        raise ValueError("配分区分がありません。")
    weight_total = sum(weights[key] for key in ordered_keys)
    if weight_total <= 0:
        raise ValueError("配分比率の合計は0より大きい必要があります。")
    normalized = {key: weights[key] / weight_total for key in ordered_keys}
    raw = {key: total * normalized[key] for key in ordered_keys}
    allocated = {key: math.floor(raw[key]) for key in ordered_keys}
    remainder = total - sum(allocated.values())
    order_index = {key: index for index, key in enumerate(ordered_keys)}
    order = sorted(
        ordered_keys,
        key=lambda key: (raw[key] - allocated[key], -order_index[key]),
        reverse=True,
    )
    for key in order[:remainder]:
        allocated[key] += 1
    return allocated


def estimate_arrivals_from_trip_ends(trip_ends: float) -> int:
    """日内の発生・集中均衡を仮定し、人TEの半分を到着人トリップへ丸める。"""
    if trip_ends < 0:
        raise ValueError("発生集中交通量は0以上である必要があります。")
    return int(math.floor((trip_ends / 2.0) + 0.5))


def _gender_items(
    counts: dict[tuple[str, str], int],
    percentages: dict[tuple[str, str], float],
    age: str,
) -> list[dict[str, Any]]:
    return [
        {
            "key": gender,
            "name": GENDER_LABELS[gender],
            "count": counts[(age, gender)],
            "share": percentages[(age, gender)],
        }
        for gender in GENDER_KEYS
    ]


def build_age_distribution(results: list[Any]) -> dict[str, Any]:
    """用途別結果から施設別・合算の年齢×性別到着人トリップを作る。"""
    facilities = []
    joint_keys = tuple((age, gender) for age in AGE_GROUPS for gender in GENDER_KEYS)
    combined_counts = {key: 0 for key in joint_keys}

    for result in results:
        key = RESULT_NAME_TO_KEY.get(result.name)
        if key is None:
            continue
        estimated_arrivals = estimate_arrivals_from_trip_ends(result.trip_ends)
        percentages = joint_profile_percentages(key)
        allocated = allocate_integer(estimated_arrivals, percentages, joint_keys)
        groups = []
        for age in AGE_GROUPS:
            genders = _gender_items(allocated, percentages, age)
            for gender in GENDER_KEYS:
                combined_counts[(age, gender)] += allocated[(age, gender)]
            groups.append(
                {
                    "age": age,
                    "count": sum(item["count"] for item in genders),
                    "share": sum(item["share"] for item in genders),
                    "genders": genders,
                }
            )
        facilities.append(
            {
                "key": key,
                "name": FACILITY_NAMES[key],
                "trip_ends": result.trip_ends,
                "estimated_arrivals": estimated_arrivals,
                "groups": groups,
            }
        )

    estimated_arrivals_total = sum(item["estimated_arrivals"] for item in facilities)
    combined_groups = []
    for age in AGE_GROUPS:
        genders = [
            {
                "key": gender,
                "name": GENDER_LABELS[gender],
                "count": combined_counts[(age, gender)],
                "share": (
                    combined_counts[(age, gender)] / estimated_arrivals_total
                    if estimated_arrivals_total
                    else 0.0
                ),
            }
            for gender in GENDER_KEYS
        ]
        combined_groups.append(
            {
                "age": age,
                "count": sum(item["count"] for item in genders),
                "share": (
                    sum(item["count"] for item in genders) / estimated_arrivals_total
                    if estimated_arrivals_total
                    else 0.0
                ),
                "genders": genders,
            }
        )

    gender_totals = [
        {
            "key": gender,
            "name": GENDER_LABELS[gender],
            "count": sum(combined_counts[(age, gender)] for age in AGE_GROUPS),
        }
        for gender in GENDER_KEYS
    ]
    return {
        "basis": "trip_ends_divided_by_two",
        "unit": "到着人トリップ/日（推定）",
        "is_unique_people": False,
        "estimated_arrivals_total": estimated_arrivals_total,
        "genders": gender_totals,
        "facilities": facilities,
        "combined_groups": combined_groups,
        "reference_file": REFERENCE_PATH.name,
        "source": "第6回中京都市圏PT調査・施設別の年齢×性別到着トリップ構成比",
        "note": (
            "発生集中交通量の半分を到着人トリップの近似とし、施設別の年齢×性別同時構成比で整数配分。"
            "ユニーク来訪者数ではない。"
        ),
    }
