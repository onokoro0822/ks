#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""複数の開発シナリオをJSONから読み、交通量と年齢×性別分布を生成する。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from html import escape
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Any
from zoneinfo import ZoneInfo

import calculate_facility_trips as calculations
from age_distribution import build_age_distribution


APP_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_PATH = APP_DIR / "nagoya_three_development_scenarios.json"
DEFAULT_OUTPUT_PATH = APP_DIR / "nagoya_three_development_scenario_distributions.json"
DEFAULT_CHART_OUTPUT_PATH = APP_DIR / "output" / "nagoya_six_scenarios_age_gender_en.png"
DEFAULT_DEMAND_CHART_OUTPUT_PATH = APP_DIR / "output" / "nagoya_six_scenarios_arrivals_by_use_en.png"
DEFAULT_INDIVIDUAL_CHART_OUTPUT_DIR = APP_DIR / "output" / "scenario_charts"
DEFAULT_HISTORY_OUTPUT_DIR = APP_DIR / "output" / "history"
FACILITY_KEYS = ("commercial", "office", "hotel")
CHART_COLORS = {
    "male": "#3b82f6",
    "female": "#f97316",
    "other": "#64748b",
}
FACILITY_CHART_COLORS = {
    "commercial": "#0f766e",
    "office": "#2563eb",
    "hotel": "#d97706",
}


def _allocate_area_from_percentages(
    total_area_m2: int,
    allocation_percent: dict[str, float],
) -> dict[str, int]:
    """用途構成比を最大剰余法で整数㎡へ変換する。"""
    raw_areas = {
        key: total_area_m2 * allocation_percent[key] / 100
        for key in FACILITY_KEYS
    }
    allocated = {key: math.floor(raw_areas[key]) for key in FACILITY_KEYS}
    remaining = total_area_m2 - sum(allocated.values())
    priority = sorted(
        FACILITY_KEYS,
        key=lambda key: (
            -(raw_areas[key] - allocated[key]),
            FACILITY_KEYS.index(key),
        ),
    )
    for key in priority[:remaining]:
        allocated[key] += 1
    return allocated


def load_scenarios(path: Path = DEFAULT_INPUT_PATH) -> dict[str, Any]:
    """シナリオJSONを読み込み、必須項目と値域を検査する。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"シナリオJSONが見つかりません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"シナリオJSONの形式が不正です: {exc}") from exc

    if payload.get("schema_version") != 1:
        raise ValueError("対応していないシナリオJSONのschema_versionです。")
    if payload.get("area_unit") != "m2":
        raise ValueError("シナリオJSONのarea_unitはm2である必要があります。")
    conditions = payload.get("calculation_conditions")
    if not isinstance(conditions, dict):
        raise ValueError("calculation_conditionsがありません。")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenariosがありません。")

    seen_ids = set()
    reference = payload.get("planning_scale_reference")
    expected_total: int | None = None
    if isinstance(reference, dict):
        expected_total = reference.get("scenario_total_area_m2")
        if (
            expected_total is not None
            and (
                isinstance(expected_total, bool)
                or not isinstance(expected_total, int)
                or expected_total <= 0
            )
        ):
            raise ValueError("scenario_total_area_m2が不正です。")
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("scenarioはJSONオブジェクトである必要があります。")
        scenario_id = str(scenario.get("id") or "").strip()
        if not scenario_id or scenario_id in seen_ids:
            raise ValueError("scenarioのidが空または重複しています。")
        seen_ids.add(scenario_id)
        if not str(scenario.get("name") or "").strip():
            raise ValueError(f"{scenario_id}のnameがありません。")
        allocation_percent = scenario.get("allocation_percent")
        if not isinstance(allocation_percent, dict):
            raise ValueError(f"{scenario_id}のallocation_percentがありません。")
        for facility_key in FACILITY_KEYS:
            value = allocation_percent.get(facility_key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                or value > 100
            ):
                raise ValueError(f"{scenario_id}の{facility_key}用途構成比が不正です。")
        if not math.isclose(
            sum(allocation_percent[key] for key in FACILITY_KEYS),
            100,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError(f"{scenario_id}の用途構成比合計は100%である必要があります。")
        if expected_total is None:
            raise ValueError("scenario_total_area_m2がありません。")
        calculated_areas = _allocate_area_from_percentages(
            expected_total,
            allocation_percent,
        )
        areas = scenario.get("floor_area_m2")
        if areas is not None and areas != calculated_areas:
            raise ValueError(
                f"{scenario_id}のfloor_area_m2がallocation_percentから算出した値と一致しません。"
            )
        scenario["floor_area_m2"] = calculated_areas
        areas = calculated_areas
        if sum(areas[key] for key in FACILITY_KEYS) <= 0:
            raise ValueError(f"{scenario_id}の用途別面積合計は0より大きい必要があります。")
        if expected_total is not None and not math.isclose(
            sum(areas[key] for key in FACILITY_KEYS),
            expected_total,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                f"{scenario_id}の用途別面積合計が"
                "scenario_total_area_m2と一致しません。"
            )
    return payload


def _calculate_scenario(
    scenario: dict[str, Any],
    conditions: dict[str, Any],
) -> dict[str, Any]:
    areas = scenario["floor_area_m2"]
    commercial_conditions = conditions["commercial"]
    office_conditions = conditions["office"]
    station_distance = conditions["station_distance_m"]
    results = [
        calculations.calculate_commercial(
            area_m2=areas["commercial"],
            area_type=commercial_conditions["area_type"],
            day=commercial_conditions["day"],
            station_distance_m=station_distance,
        ),
        calculations.calculate_office(
            area_m2=areas["office"],
            location_key=office_conditions["location_key"],
            building_key=office_conditions["building_key"],
            commercial_floor_ratio=office_conditions["commercial_floor_ratio_pct"],
            station_distance_m=station_distance,
        ),
        calculations.calculate_hotel(area_m2=areas["hotel"]),
    ]
    total = sum(result.trip_ends for result in results)
    internal_trip_reduction = 0.0
    adjusted_total = None
    if conditions.get("apply_internal_trip_reduction"):
        by_name = {result.name: result for result in results}
        if areas["commercial"] > 0 and areas["office"] > 0:
            internal_trip_reduction = (
                by_name["商業施設"].trip_ends + by_name["オフィス"].trip_ends
            ) * 0.05
        adjusted_total = total - internal_trip_reduction

    return {
        "id": scenario["id"],
        "number": scenario.get("number"),
        "name": scenario["name"],
        "name_en": scenario.get("name_en", scenario["name"]),
        "aim": scenario.get("aim", ""),
        "aim_en": scenario.get("aim_en", scenario.get("aim", "")),
        "reference_facility": scenario.get("reference_facility", ""),
        "reference_facility_en": scenario.get(
            "reference_facility_en",
            scenario.get("reference_facility", ""),
        ),
        "allocation_basis": scenario.get("allocation_basis", ""),
        "allocation_percent": scenario["allocation_percent"],
        "floor_area_m2": areas,
        "facility_results": [
            {
                "key": FACILITY_KEYS[index],
                "name": result.name,
                "category": result.category,
                "area_m2": result.area_m2,
                "applied_rate": result.applied_rate,
                "trip_ends": result.trip_ends,
            }
            for index, result in enumerate(results)
        ],
        "total_trip_ends": total,
        "internal_trip_reduction": internal_trip_reduction,
        "adjusted_total_trip_ends": adjusted_total,
        "age_gender_distribution": build_age_distribution(results),
    }


def build_scenario_distributions(payload: dict[str, Any]) -> dict[str, Any]:
    """検査済みシナリオから各案の計算結果と属性分布を返す。"""
    conditions = payload["calculation_conditions"]
    return {
        "schema_version": 1,
        "title": payload.get("title", ""),
        "source_scenario_file": DEFAULT_INPUT_PATH.name,
        "distribution_reference": payload["distribution_reference"],
        "site_scope": payload.get("site_scope"),
        "planning_scale_reference": payload.get("planning_scale_reference"),
        "allocation_method": payload.get("allocation_method"),
        "calculation_conditions": conditions,
        "note": (
            "各案の合計54,374㎡は旧本店の売場面積相当を比較用床面積として置いた暫定値であり、"
            "新計画の延床面積ではない。"
            "用途構成比は利用者が指定した比較・感度分析条件であり、確定計画ではない。"
            "年齢×性別分布は施設別の未調整人TEを2で割った推定到着人トリップへ適用。"
            "内々交通5%低減は商業・オフィスの両用途がある案だけに適用する。"
            "調整後合計には用途別・属性別の配分根拠がないため、年齢×性別分布へ適用していない。"
        ),
        "scenarios": [
            _calculate_scenario(scenario, conditions)
            for scenario in payload["scenarios"]
        ],
    }


def _nice_axis_max(value: int) -> int:
    """棒グラフの最大値を読みやすい切りのよい値へ切り上げる。"""
    if value <= 0:
        return 1
    magnitude = 10 ** max(0, len(str(value)) - 2)
    return int(math.ceil(value / magnitude) * magnitude)


def _english_age_label(label: str) -> str:
    return {
        "10歳未満": "0–9",
        "10～19歳": "10–19",
        "20～29歳": "20–29",
        "30～39歳": "30–39",
        "40～49歳": "40–49",
        "50～59歳": "50–59",
        "60～69歳": "60–69",
        "70～79歳": "70–79",
        "80歳以上": "80+",
    }.get(label, label)


def _build_single_age_gender_chart_svg(
    scenario: dict[str, Any],
    distribution_reference: str,
) -> str:
    """1シナリオ分の年齢×性別構成比と推定値を独立した英語SVGへする。"""
    width = 1400
    height = 1080
    plot_left = 120
    plot_right = width - 50
    plot_top = 190
    plot_bottom = 675
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top
    distribution = scenario["age_gender_distribution"]
    total = distribution["estimated_arrivals_total"]
    groups = distribution["combined_groups"]
    shares = [100.0 * group["count"] / total if total else 0.0 for group in groups]
    axis_max = max(20, int(math.ceil(max(shares, default=0.0) / 5.0) * 5) + 5)
    reference_facility = scenario.get("reference_facility_en", "")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,Helvetica,sans-serif;fill:#172033}",
        ".title{font-size:34px;font-weight:700}.subtitle{font-size:18px;fill:#526078}",
        ".axis{font-size:16px;fill:#526078}.value{font-size:14px;font-weight:700;fill:#334155}",
        ".legend{font-size:17px;fill:#334155}.table-head{font-size:14px;font-weight:700;fill:#334155}",
        ".table-value{font-size:15px;fill:#172033}.table-label{font-size:15px;font-weight:700;fill:#334155}",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="50" y="52" class="title">Age–gender composition — {scenario.get("number")}. {escape(scenario["name_en"])}</text>',
        f'<text x="50" y="88" class="subtitle">Estimated arrivals: {total:,} trips/day; shares use facility-specific profiles from the 2022 Chukyo Person Trip Survey.</text>',
    ]
    if reference_facility:
        parts.append(
            f'<text x="50" y="121" class="subtitle">Reference facility: {escape(reference_facility)}</text>'
        )
    legend_items = (("male", "Male"), ("female", "Female"), ("other", "Other / unknown"))
    for index, (key, label) in enumerate(legend_items):
        x = width - 550 + index * 175
        parts.append(f'<rect x="{x}" y="113" width="20" height="20" rx="2" fill="{CHART_COLORS[key]}"/>')
        parts.append(f'<text x="{x + 29}" y="130" class="legend">{label}</text>')
    for tick in range(6):
        value = axis_max * tick / 5
        y = plot_bottom - plot_height * tick / 5
        parts.append(f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{plot_left - 14}" y="{y + 6:.1f}" text-anchor="end" class="axis">{value:.0f}%</text>')
    slot_width = plot_width / len(groups)
    bar_width = slot_width * 0.62
    for group_index, group in enumerate(groups):
        x = plot_left + group_index * slot_width + (slot_width - bar_width) / 2
        stack_y = plot_bottom
        group_share = 100.0 * group["count"] / total if total else 0.0
        for gender in group["genders"]:
            share = 100.0 * gender["count"] / total if total else 0.0
            bar_height = plot_height * share / axis_max
            stack_y -= bar_height
            parts.append(f'<rect x="{x:.1f}" y="{stack_y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{CHART_COLORS[gender["key"]]}"/>')
        parts.append(f'<text x="{x + bar_width / 2:.1f}" y="{plot_bottom + 27:.1f}" text-anchor="middle" class="axis">{_english_age_label(group["age"])}</text>')
        label_x = x + bar_width / 2
        parts.append(
            f'<text x="{label_x:.1f}" y="{stack_y - 25:.1f}" text-anchor="middle" class="value">{group_share:.1f}%</text>'
        )
        parts.append(
            f'<text x="{label_x:.1f}" y="{stack_y - 8:.1f}" text-anchor="middle" class="value">n={group["count"]:,}</text>'
        )
    parts.append(f'<text x="34" y="{plot_top + plot_height / 2:.1f}" text-anchor="middle" class="axis" transform="rotate(-90 34 {plot_top + plot_height / 2:.1f})">Share of estimated arrivals</text>')

    table_left = plot_left
    table_top = 770
    table_width = plot_width
    label_width = 150
    header_height = 38
    row_height = 44
    data_width = (table_width - label_width) / len(groups)
    table_bottom = table_top + header_height + row_height * len(legend_items)
    parts.append(
        f'<text x="{table_left}" y="{table_top - 18}" class="table-head">Estimated arrival trips/day by age and gender</text>'
    )
    parts.append(
        f'<rect x="{table_left}" y="{table_top}" width="{table_width}" height="{table_bottom - table_top}" fill="#ffffff" stroke="#cbd5e1"/>'
    )
    parts.append(
        f'<rect x="{table_left}" y="{table_top}" width="{table_width}" height="{header_height}" fill="#f1f5f9"/>'
    )
    for row_index in range(len(legend_items) + 1):
        y = table_top + header_height + row_index * row_height
        parts.append(
            f'<line x1="{table_left}" y1="{y:.1f}" x2="{table_left + table_width}" y2="{y:.1f}" stroke="#cbd5e1"/>'
        )
    parts.append(
        f'<line x1="{table_left + label_width}" y1="{table_top}" x2="{table_left + label_width}" y2="{table_bottom}" stroke="#cbd5e1"/>'
    )
    parts.append(
        f'<text x="{table_left + 14}" y="{table_top + 25}" class="table-head">Gender</text>'
    )
    for group_index, group in enumerate(groups):
        center_x = table_left + label_width + data_width * (group_index + 0.5)
        if group_index:
            x = table_left + label_width + data_width * group_index
            parts.append(
                f'<line x1="{x:.1f}" y1="{table_top}" x2="{x:.1f}" y2="{table_bottom}" stroke="#e2e8f0"/>'
            )
        parts.append(
            f'<text x="{center_x:.1f}" y="{table_top + 25}" text-anchor="middle" class="table-head">{_english_age_label(group["age"])}</text>'
        )
    for row_index, (gender_key, gender_label) in enumerate(legend_items):
        center_y = table_top + header_height + row_height * row_index + row_height / 2
        parts.append(
            f'<rect x="{table_left + 14}" y="{center_y - 8:.1f}" width="16" height="16" rx="2" fill="{CHART_COLORS[gender_key]}"/>'
        )
        parts.append(
            f'<text x="{table_left + 40}" y="{center_y + 6:.1f}" class="table-label">{gender_label}</text>'
        )
        for group_index, group in enumerate(groups):
            counts = {gender["key"]: gender["count"] for gender in group["genders"]}
            center_x = table_left + label_width + data_width * (group_index + 0.5)
            parts.append(
                f'<text x="{center_x:.1f}" y="{center_y + 6:.1f}" text-anchor="middle" class="table-value">{counts.get(gender_key, 0):,}</text>'
            )
    parts.append(f'<text x="{width / 2:.1f}" y="{height - 35}" text-anchor="middle" class="subtitle">Source: {escape(distribution_reference)}. Arrival trips ≈ person-trip ends ÷ 2; values are not unique visitors.</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _build_single_demand_chart_svg(scenario: dict[str, Any]) -> str:
    """1シナリオ分の用途別推定到着量を独立した英語SVGへする。"""
    width = 1400
    height = 800
    plot_left = 260
    plot_right = width - 90
    plot_top = 230
    plot_bottom = 640
    plot_width = plot_right - plot_left
    facilities = {
        item["key"]: item
        for item in scenario["age_gender_distribution"]["facilities"]
    }
    values = [facilities[key]["estimated_arrivals"] for key in FACILITY_KEYS]
    axis_max = _nice_axis_max(max(values, default=0))
    labels = {
        "commercial": "Commercial",
        "office": "Office",
        "hotel": "Hotel",
    }
    shares = scenario["allocation_percent"]
    total = scenario["age_gender_distribution"]["estimated_arrivals_total"]
    reference_facility = scenario.get("reference_facility_en", "")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,Helvetica,sans-serif;fill:#172033}",
        ".title{font-size:34px;font-weight:700}.subtitle{font-size:18px;fill:#526078}",
        ".axis{font-size:17px;fill:#526078}.label{font-size:20px;font-weight:700}",
        ".value{font-size:19px;font-weight:700}",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="50" y="52" class="title">Estimated daily arrivals by use — {scenario.get("number")}. {escape(scenario["name_en"])}</text>',
        f'<text x="50" y="88" class="subtitle">Program: commercial {shares["commercial"]:g}%, office {shares["office"]:g}%, hotel {shares["hotel"]:g}% of the 54,374 m² comparison area.</text>',
        f'<text x="50" y="125" class="subtitle">Total estimated arrivals: {total:,} trips/day</text>',
    ]
    if reference_facility:
        parts.append(
            f'<text x="50" y="160" class="subtitle">Reference facility: {escape(reference_facility)}</text>'
        )
    for tick in range(6):
        value = axis_max * tick / 5
        x = plot_left + plot_width * tick / 5
        parts.append(f'<line x1="{x:.1f}" y1="{plot_top}" x2="{x:.1f}" y2="{plot_bottom}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{x:.1f}" y="{plot_bottom + 35}" text-anchor="middle" class="axis">{value:,.0f}</text>')
    row_height = (plot_bottom - plot_top) / len(FACILITY_KEYS)
    bar_height = 72
    for index, key in enumerate(FACILITY_KEYS):
        value = facilities[key]["estimated_arrivals"]
        y = plot_top + index * row_height + (row_height - bar_height) / 2
        bar_width = plot_width * value / axis_max if axis_max else 0
        parts.append(f'<text x="{plot_left - 24}" y="{y + bar_height / 2 + 7:.1f}" text-anchor="end" class="label">{labels[key]}</text>')
        if bar_width > 0:
            parts.append(f'<rect x="{plot_left}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height}" rx="4" fill="{FACILITY_CHART_COLORS[key]}"/>')
        parts.append(f'<text x="{plot_left + bar_width + 14:.1f}" y="{y + bar_height / 2 + 7:.1f}" class="value">{value:,}</text>')
    parts.append(f'<text x="{(plot_left + plot_right) / 2:.1f}" y="{height - 80}" text-anchor="middle" class="axis">Estimated arrival trips per day</text>')
    parts.append(f'<text x="{width / 2:.1f}" y="{height - 32}" text-anchor="middle" class="subtitle">Method: facility-specific person-trip-end rates; arrivals ≈ person-trip ends ÷ 2. Results are not unique visitor counts.</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _build_chart_svg(result: dict[str, Any]) -> str:
    """研究資料向けの英語年齢×性別構成比グラフを2×3配置で作る。"""
    scenarios = result["scenarios"]
    width = 1800
    height = 1160
    margin_x = 48
    top_margin = 142
    footer_height = 54
    column_gap = 28
    row_gap = 24
    columns = 2
    rows = math.ceil(len(scenarios) / columns)
    panel_width = (width - margin_x * 2 - column_gap) / columns
    panel_height = (height - top_margin - footer_height - row_gap * (rows - 1)) / rows
    all_shares = []
    for scenario in scenarios:
        total = scenario["age_gender_distribution"]["estimated_arrivals_total"]
        for group in scenario["age_gender_distribution"]["combined_groups"]:
            all_shares.append(100.0 * group["count"] / total if total else 0.0)
    axis_max = max(20, int(math.ceil(max(all_shares, default=0.0) / 5.0) * 5) + 5)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,Helvetica,sans-serif;fill:#172033}",
        ".title{font-size:34px;font-weight:700}.subtitle{font-size:17px;fill:#526078}",
        ".panel-title{font-size:21px;font-weight:700}.axis{font-size:13px;fill:#526078}",
        ".value{font-size:11px;font-weight:700;fill:#334155}.legend{font-size:15px;fill:#334155}",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="48" y="47" class="title">Age–gender composition of estimated arrivals across six scenarios</text>',
        '<text x="48" y="78" class="subtitle">Shares use facility-specific profiles from the 2022 Chukyo Person Trip Survey; arrival trips are approximated as person-trip ends ÷ 2.</text>',
    ]
    legend_items = (("male", "Male"), ("female", "Female"), ("other", "Other / unknown"))
    legend_x = width - 530
    for index, (key, label) in enumerate(legend_items):
        x = legend_x + index * 170
        parts.append(f'<rect x="{x}" y="101" width="18" height="18" rx="2" fill="{CHART_COLORS[key]}"/>')
        parts.append(f'<text x="{x + 26}" y="116" class="legend">{label}</text>')

    for scenario_index, scenario in enumerate(scenarios):
        column = scenario_index % columns
        row = scenario_index // columns
        panel_x = margin_x + column * (panel_width + column_gap)
        panel_y = top_margin + row * (panel_height + row_gap)
        plot_left = panel_x + 68
        plot_right = panel_x + panel_width - 24
        plot_top = panel_y + 58
        plot_bottom = panel_y + panel_height - 44
        plot_width = plot_right - plot_left
        plot_height = plot_bottom - plot_top
        total = scenario["age_gender_distribution"]["estimated_arrivals_total"]
        parts.extend([
            f'<rect x="{panel_x:.1f}" y="{panel_y:.1f}" width="{panel_width:.1f}" height="{panel_height:.1f}" rx="10" fill="#ffffff" stroke="#d8e0ea"/>',
            f'<text x="{panel_x + 20:.1f}" y="{panel_y + 31:.1f}" class="panel-title">{scenario.get("number")}. {escape(scenario["name_en"])}</text>',
            f'<text x="{panel_x + panel_width - 20:.1f}" y="{panel_y + 30:.1f}" text-anchor="end" class="subtitle">n = {total:,} arrival trips/day</text>',
        ])
        for tick in range(4):
            value = axis_max * tick / 3
            y = plot_bottom - plot_height * tick / 3
            parts.append(f'<line x1="{plot_left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}" stroke="#e6ebf1"/>')
            parts.append(f'<text x="{plot_left - 10:.1f}" y="{y + 4:.1f}" text-anchor="end" class="axis">{value:.0f}%</text>')
        groups = scenario["age_gender_distribution"]["combined_groups"]
        slot_width = plot_width / len(groups)
        bar_width = slot_width * 0.62
        for group_index, group in enumerate(groups):
            x = plot_left + group_index * slot_width + (slot_width - bar_width) / 2
            stack_y = plot_bottom
            group_share = 100.0 * group["count"] / total if total else 0.0
            for gender in group["genders"]:
                share = 100.0 * gender["count"] / total if total else 0.0
                bar_height = plot_height * share / axis_max
                stack_y -= bar_height
                parts.append(f'<rect x="{x:.1f}" y="{stack_y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{CHART_COLORS[gender["key"]]}"/>')
            parts.append(f'<text x="{x + bar_width / 2:.1f}" y="{plot_bottom + 19:.1f}" text-anchor="middle" class="axis">{_english_age_label(group["age"])}</text>')
            label_x = x + bar_width / 2
            parts.append(
                f'<text x="{label_x:.1f}" y="{stack_y - 15:.1f}" text-anchor="middle" class="value">{group_share:.1f}%</text>'
            )
            parts.append(
                f'<text x="{label_x:.1f}" y="{stack_y - 3:.1f}" text-anchor="middle" class="value">n={group["count"]:,}</text>'
            )
        if column == 0:
            parts.append(f'<text x="{panel_x + 18:.1f}" y="{plot_top + plot_height / 2:.1f}" text-anchor="middle" class="axis" transform="rotate(-90 {panel_x + 18:.1f} {plot_top + plot_height / 2:.1f})">Share of arrivals</text>')

    parts.append(f'<text x="{width / 2:.1f}" y="{height - 18}" text-anchor="middle" class="subtitle">Source: {escape(result["distribution_reference"])}. Values are estimated arrival trips, not unique visitors.</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _build_demand_chart_svg(result: dict[str, Any]) -> str:
    """研究資料向けの用途別推定到着量積み上げ棒グラフを作る。"""
    scenarios = result["scenarios"]
    width = 1800
    height = 980
    plot_left = 150
    plot_right = width - 60
    plot_top = 160
    plot_bottom = 810
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top
    totals = [item["age_gender_distribution"]["estimated_arrivals_total"] for item in scenarios]
    axis_max = _nice_axis_max(max(totals, default=0))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,Helvetica,sans-serif;fill:#172033}",
        ".title{font-size:36px;font-weight:700}.subtitle{font-size:18px;fill:#526078}",
        ".axis{font-size:16px;fill:#526078}.value{font-size:17px;font-weight:700}",
        ".legend{font-size:17px;fill:#334155}",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="55" y="52" class="title">Estimated daily arrivals vary substantially by development program</text>',
        '<text x="55" y="88" class="subtitle">Six scenarios use the same 54,374 m² comparison area; stacked bars show the contribution of each facility use.</text>',
    ]
    legend_items = (("commercial", "Commercial"), ("office", "Office"), ("hotel", "Hotel"))
    legend_x = width - 560
    for index, (key, label) in enumerate(legend_items):
        x = legend_x + index * 180
        parts.append(f'<rect x="{x}" y="112" width="20" height="20" rx="2" fill="{FACILITY_CHART_COLORS[key]}"/>')
        parts.append(f'<text x="{x + 29}" y="129" class="legend">{label}</text>')
    for tick in range(6):
        value = axis_max * tick / 5
        y = plot_bottom - plot_height * tick / 5
        parts.append(f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{plot_left - 14}" y="{y + 6:.1f}" text-anchor="end" class="axis">{value:,.0f}</text>')
    slot_width = plot_width / len(scenarios)
    bar_width = min(150.0, slot_width * 0.58)
    for scenario_index, scenario in enumerate(scenarios):
        x = plot_left + scenario_index * slot_width + (slot_width - bar_width) / 2
        stack_y = plot_bottom
        facilities = {item["key"]: item for item in scenario["age_gender_distribution"]["facilities"]}
        for facility_key in FACILITY_KEYS:
            value = facilities[facility_key]["estimated_arrivals"]
            bar_height = plot_height * value / axis_max
            stack_y -= bar_height
            if bar_height > 0:
                parts.append(f'<rect x="{x:.1f}" y="{stack_y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{FACILITY_CHART_COLORS[facility_key]}"/>')
        total = scenario["age_gender_distribution"]["estimated_arrivals_total"]
        parts.append(f'<text x="{x + bar_width / 2:.1f}" y="{stack_y - 12:.1f}" text-anchor="middle" class="value">{total:,}</text>')
        parts.append(f'<text x="{x + bar_width / 2:.1f}" y="{plot_bottom + 35:.1f}" text-anchor="middle" class="axis">{scenario.get("number")}. {escape(scenario["name_en"])}</text>')
    parts.append(f'<text x="35" y="{plot_top + plot_height / 2:.1f}" text-anchor="middle" class="axis" transform="rotate(-90 35 {plot_top + plot_height / 2:.1f})">Estimated arrival trips per day</text>')
    parts.append(f'<text x="{width / 2:.1f}" y="{height - 34}" text-anchor="middle" class="subtitle">Method: facility-specific person-trip-end rates; arrivals ≈ person-trip ends ÷ 2. Results are not unique visitor counts.</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _save_svg_chart(svg: str, output_path: Path) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".svg":
        output_path.write_text(svg, encoding="utf-8")
        return output_path
    if output_path.suffix.lower() != ".png":
        raise ValueError("棒グラフ画像の拡張子は.pngまたは.svgにしてください。")
    svg_path = output_path.with_suffix(".svg")
    svg_path.write_text(svg, encoding="utf-8")
    sips = shutil.which("sips")
    direct_detail = "sipsが見つかりません。"
    if sips is not None:
        completed = subprocess.run(
            [sips, "-s", "format", "png", str(svg_path), "--out", str(output_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0 and output_path.exists():
            return output_path
        direct_detail = completed.stderr.strip() or completed.stdout.strip()

    # macOSのsipsがSVGを直接読めない環境ではQuick Lookで一度PNG化し、
    # 元SVGの縦横比へ中央クロップする。
    qlmanage = shutil.which("qlmanage")
    size_match = re.search(r'<svg[^>]+width="(\d+)"[^>]+height="(\d+)"', svg)
    if qlmanage is not None and sips is not None and size_match is not None:
        width, height = (int(value) for value in size_match.groups())
        with TemporaryDirectory() as directory:
            thumbnail_dir = Path(directory)
            quicklook = subprocess.run(
                [
                    qlmanage,
                    "-t",
                    "-s",
                    str(max(width, height)),
                    "-o",
                    str(thumbnail_dir),
                    str(svg_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            thumbnail = thumbnail_dir / f"{svg_path.name}.png"
            if quicklook.returncode == 0 and thumbnail.exists():
                cropped = subprocess.run(
                    [
                        sips,
                        "-c",
                        str(height),
                        str(width),
                        str(thumbnail),
                        "--out",
                        str(output_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if cropped.returncode == 0 and output_path.exists():
                    return output_path
                quicklook_detail = cropped.stderr.strip() or cropped.stdout.strip()
            else:
                quicklook_detail = quicklook.stderr.strip() or quicklook.stdout.strip()
        raise RuntimeError(
            "PNG画像の保存に失敗しました: "
            f"sips={direct_detail}; Quick Look={quicklook_detail}（SVG: {svg_path}）"
        )
    raise RuntimeError(
        f"PNG画像の保存に失敗しました: {direct_detail}（SVGは保存済み: {svg_path}）"
    )


def save_scenario_chart(
    result: dict[str, Any],
    output_path: Path = DEFAULT_CHART_OUTPUT_PATH,
) -> Path:
    """英語の年齢×性別構成比グラフをPNG・SVGで保存する。"""
    return _save_svg_chart(_build_chart_svg(result), output_path)


def save_demand_chart(
    result: dict[str, Any],
    output_path: Path = DEFAULT_DEMAND_CHART_OUTPUT_PATH,
) -> Path:
    """英語の用途別推定到着量グラフをPNG・SVGで保存する。"""
    return _save_svg_chart(_build_demand_chart_svg(result), output_path)


def save_individual_scenario_charts(
    result: dict[str, Any],
    output_dir: Path = DEFAULT_INDIVIDUAL_CHART_OUTPUT_DIR,
) -> dict[str, dict[str, Path]]:
    """各シナリオの用途別到着量と年齢×性別を個別PNG・SVGへ保存する。"""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {
        f"{int(scenario.get('number') or 0):02d}_{scenario['id']}_{chart_type}_en.{suffix}"
        for scenario in result["scenarios"]
        for chart_type in ("age_gender", "arrivals_by_use")
        for suffix in ("png", "svg")
    }
    generated_chart_pattern = re.compile(
        r"\d{2}_.+_(?:age_gender|arrivals_by_use)_en\.(?:png|svg)"
    )
    for existing in output_dir.iterdir():
        if (
            existing.is_file()
            and generated_chart_pattern.fullmatch(existing.name)
            and existing.name not in expected_names
        ):
            existing.unlink()
    saved: dict[str, dict[str, Path]] = {}
    for scenario in result["scenarios"]:
        number = int(scenario.get("number") or 0)
        stem = f"{number:02d}_{scenario['id']}"
        age_png = output_dir / f"{stem}_age_gender_en.png"
        demand_png = output_dir / f"{stem}_arrivals_by_use_en.png"
        _save_svg_chart(
            _build_single_age_gender_chart_svg(
                scenario,
                result["distribution_reference"],
            ),
            age_png,
        )
        _save_svg_chart(
            _build_single_demand_chart_svg(scenario),
            demand_png,
        )
        saved[scenario["id"]] = {
            "age_gender_png": age_png,
            "age_gender_svg": age_png.with_suffix(".svg"),
            "arrivals_by_use_png": demand_png,
            "arrivals_by_use_svg": demand_png.with_suffix(".svg"),
        }
    return saved


def _chart_path_for_json(path: Path) -> str:
    """アプリ内の画像は相対パス、外部出力は絶対パスでJSONへ記録する。"""
    try:
        return str(path.resolve().relative_to(APP_DIR))
    except ValueError:
        return str(path.resolve())


def _resolve_distribution_reference_path(
    input_path: Path,
    payload: dict[str, Any],
) -> Path:
    """シナリオが参照する年齢・性別分布JSONの実ファイルを返す。"""
    reference = Path(payload["distribution_reference"])
    if reference.is_absolute():
        return reference.resolve()
    beside_input = input_path.resolve().parent / reference
    if beside_input.is_file():
        return beside_input
    return (APP_DIR / reference).resolve()


def _input_fingerprint(paths: list[Path]) -> str:
    """計算入力の内容をSHA-256で識別する。"""
    digest = hashlib.sha256()
    for path in paths:
        resolved = path.resolve()
        digest.update(resolved.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _find_history_archive(history_dir: Path, fingerprint: str) -> Path | None:
    """同じ入力で既に作成済みの履歴があれば返す。"""
    if not history_dir.is_dir():
        return None
    for manifest_path in sorted(history_dir.glob("*/manifest.json"), reverse=True):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("input_fingerprint") == fingerprint:
            return manifest_path.parent
    return None


def _new_history_archive_path(history_dir: Path, generated_at: datetime) -> Path:
    """同一秒の実行にも衝突しない履歴フォルダ名を返す。"""
    stem = generated_at.strftime("%Y%m%d_%H%M%S")
    candidate = history_dir / stem
    suffix = 2
    while candidate.exists():
        candidate = history_dir / f"{stem}_{suffix:02d}"
        suffix += 1
    return candidate


def _archive_generated_outputs(
    archive_dir: Path,
    input_paths: list[Path],
    output_path: Path,
    chart_paths: list[Path],
    generated_at: datetime,
    fingerprint: str,
) -> None:
    """成功した計算の入力・結果・画像を一つの履歴フォルダへ保存する。"""
    inputs_dir = archive_dir / "inputs"
    charts_dir = archive_dir / "charts"
    scenario_charts_dir = charts_dir / "scenario_charts"
    inputs_dir.mkdir(parents=True, exist_ok=False)
    charts_dir.mkdir(parents=True, exist_ok=True)
    scenario_charts_dir.mkdir(parents=True, exist_ok=True)
    copied_files: list[str] = []
    for source in input_paths:
        destination = inputs_dir / source.name
        shutil.copy2(source, destination)
        copied_files.append(str(destination.relative_to(archive_dir)))
    result_destination = archive_dir / output_path.name
    shutil.copy2(output_path, result_destination)
    copied_files.append(str(result_destination.relative_to(archive_dir)))
    for source in chart_paths:
        if not source.is_file():
            continue
        destination_dir = (
            scenario_charts_dir
            if source.parent.name == DEFAULT_INDIVIDUAL_CHART_OUTPUT_DIR.name
            else charts_dir
        )
        destination = destination_dir / source.name
        shutil.copy2(source, destination)
        copied_files.append(str(destination.relative_to(archive_dir)))
    manifest = {
        "schema_version": 1,
        "generated_at_jst": generated_at.isoformat(),
        "input_fingerprint": fingerprint,
        "history_policy": "on_input_change",
        "files": copied_files,
    }
    (archive_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def generate_distributions(
    input_path: Path = DEFAULT_INPUT_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    chart_output_path: Path | None = DEFAULT_CHART_OUTPUT_PATH,
    demand_chart_output_path: Path | None = DEFAULT_DEMAND_CHART_OUTPUT_PATH,
    individual_chart_output_dir: Path | None = DEFAULT_INDIVIDUAL_CHART_OUTPUT_DIR,
    history_output_dir: Path = DEFAULT_HISTORY_OUTPUT_DIR,
    archive_history: bool = True,
) -> dict[str, Any]:
    """入力JSONを読み、結果JSONと研究資料用の個別PNG・SVGを書き出す。"""
    input_path = input_path.resolve()
    payload = load_scenarios(input_path)
    distribution_reference_path = _resolve_distribution_reference_path(
        input_path,
        payload,
    )
    input_paths = [input_path, distribution_reference_path]
    fingerprint = _input_fingerprint(input_paths)
    generated_at = datetime.now(ZoneInfo("Asia/Tokyo"))
    history_output_dir = history_output_dir.resolve()
    existing_archive = (
        _find_history_archive(history_output_dir, fingerprint)
        if archive_history
        else None
    )
    archive_dir = (
        existing_archive
        if existing_archive is not None
        else _new_history_archive_path(history_output_dir, generated_at)
    )
    create_archive = archive_history and existing_archive is None

    result = build_scenario_distributions(payload)
    result["source_scenario_file"] = input_path.name
    result["generation"] = {
        "generated_at_jst": generated_at.isoformat(),
        "input_fingerprint": fingerprint,
        "history_policy": "on_input_change" if archive_history else "disabled",
        "history_created": create_archive,
        "history_archive": _chart_path_for_json(archive_dir) if archive_history else "",
    }
    chart_images = {}
    generated_chart_paths: list[Path] = []
    if chart_output_path is not None:
        saved_chart = save_scenario_chart(result, chart_output_path)
        chart_images["age_gender_composition_en"] = _chart_path_for_json(saved_chart)
        generated_chart_paths.extend((saved_chart, saved_chart.with_suffix(".svg")))
    if demand_chart_output_path is not None:
        saved_demand_chart = save_demand_chart(result, demand_chart_output_path)
        chart_images["arrivals_by_use_en"] = _chart_path_for_json(saved_demand_chart)
        generated_chart_paths.extend(
            (saved_demand_chart, saved_demand_chart.with_suffix(".svg"))
        )
    if chart_images:
        result["chart_images"] = chart_images
        result["chart_image"] = chart_images.get("age_gender_composition_en", "")
    if individual_chart_output_dir is not None:
        individual_paths = save_individual_scenario_charts(
            result,
            individual_chart_output_dir,
        )
        individual_chart_images = {
            scenario_id: {
                chart_type: _chart_path_for_json(path)
                for chart_type, path in paths.items()
            }
            for scenario_id, paths in individual_paths.items()
        }
        result["individual_chart_images"] = individual_chart_images
        for scenario in result["scenarios"]:
            scenario["chart_images"] = individual_chart_images[scenario["id"]]
        generated_chart_paths.extend(
            path
            for paths in individual_paths.values()
            for path in paths.values()
        )
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if create_archive:
        _archive_generated_outputs(
            archive_dir,
            input_paths,
            output_path,
            list(dict.fromkeys(path.resolve() for path in generated_chart_paths)),
            generated_at,
            fingerprint,
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="開発各案の交通量・年齢性別分布を生成")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--chart-output", type=Path, default=DEFAULT_CHART_OUTPUT_PATH)
    parser.add_argument(
        "--demand-chart-output",
        type=Path,
        default=DEFAULT_DEMAND_CHART_OUTPUT_PATH,
    )
    parser.add_argument(
        "--individual-chart-dir",
        type=Path,
        default=DEFAULT_INDIVIDUAL_CHART_OUTPUT_DIR,
        help="シナリオ別PNG・SVGの保存フォルダ",
    )
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=DEFAULT_HISTORY_OUTPUT_DIR,
        help="入力変更時の履歴保存フォルダ",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="この実行では履歴を保存しない",
    )
    parser.add_argument("--skip-chart", action="store_true", help="棒グラフ画像を保存しない")
    args = parser.parse_args()
    chart_output = None if args.skip_chart else args.chart_output
    demand_chart_output = None if args.skip_chart else args.demand_chart_output
    individual_chart_output_dir = None if args.skip_chart else args.individual_chart_dir
    result = generate_distributions(
        args.input,
        args.output,
        chart_output,
        demand_chart_output,
        individual_chart_output_dir,
        args.history_dir,
        not args.no_history,
    )
    print(f"{len(result['scenarios'])}案を生成しました: {args.output}")
    for chart_name, chart_path in result.get("chart_images", {}).items():
        print(f"棒グラフ画像を保存しました（{chart_name}）: {chart_path}")
    if result.get("individual_chart_images"):
        print(
            "シナリオ別の用途別到着量・年齢×性別グラフを"
            f"個別保存しました: {args.individual_chart_dir}"
        )
    generation = result.get("generation", {})
    if generation.get("history_created"):
        print(f"入力変更の履歴を保存しました: {generation['history_archive']}")


if __name__ == "__main__":
    main()
