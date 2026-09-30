#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Geminiとの自然言語対話・Web調査から施設交通量の計算条件を収集する。

LLMは利用者の発話の構造化と、Google検索で公開情報を調べるために使う。
原単位、補正係数、丸め、発生集中交通量の計算は
``calculate_facility_trips.py`` の決定論的なPython関数が担当する。
"""

from __future__ import annotations

import argparse
import copy
import getpass
import json
import os
from pathlib import Path
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_TIMEOUT = 60.0


SYSTEM_PROMPT = """
あなたは、大規模開発地区の発生集中交通量を計算するための、日本語の対話型調査
アシスタントです。現在の入力状態、現在確認中の項目、会話履歴を読み、必要に応じて
Google検索で公開情報を調べ、JSONオブジェクトだけを返してください。

出力形式:
{
  "assistant_message": "回答への短い理解、調査結果、または用語質問への説明。次の定型質問は含めない",
  "turn_type": "answer または clarification または cancel",
  "updates": {
    "target_facility_name": null,
    "facility_types": ["commercial", "office", "hotel"],
    "station_distance_m": null,
    "commercial": {"area_m2": null, "area_type": null, "day": null},
    "office": {
      "area_m2": null,
      "location_key": null,
      "building_key": null,
      "commercial_floor_ratio": null
    },
    "hotel": {"area_m2": null},
    "apply_internal_trip_reduction": null,
    "confirmed": null
  },
  "web_research": {
    "used": false,
    "summary": "調べた内容と、計算値へ採用した根拠の短い説明。未使用なら空文字",
    "derived_fields": ["commercial.area_m2"]
  }
}

値の定義:
- facility_types: commercial=商業施設、office=オフィス、hotel=ホテル。計算したい用途だけ。
- commercial.area_type: a=三大都市圏中心部、b=三大都市圏郊外部および地方中枢都市、
  c=三大都市圏周辺部および地方都市。
- commercial.day: 平日または休日。
- office.location_key: a=都心部、b=周辺部。
- office.building_key: a=一般事務所ビル、b=単館型事務所ビル。
- confirmed: 全条件の確認質問に対する同意ならtrue、修正ならfalse。

厳守事項:
- 施設名・駅名・建物名などの固有名詞が出た場合、または利用者が「調べて」と依頼した場合は、
  Google検索を使い、計算に必要な床面積、駅距離、施設用途などの公開情報を探す。
- 調査では公式サイト、行政資料、事業者のニュースリリースなど一次情報を優先する。
- 延床面積、敷地面積、売場面積、営業面積を混同しない。用途別面積が必要なのに建物全体の
  延床面積しか分からない場合は、その数値を勝手に用途別面積としてupdatesへ入れない。
- 一つの対象・定義・数値を信頼できる出典で特定できた場合は、その値をupdatesへ入れ、
  web_research.derived_fieldsに該当フィールド名を入れる。曖昧・複数候補・推計の場合は
  候補と違いをassistant_messageで説明し、値を入れず利用者へ確認する。
- 利用者が明示した情報、または上記の方法でWeb上の根拠を確認した情報だけupdatesに入れる。
- 日本語の単位表現（6万㎡、1.5km、10パーセント等）は数値へ変換してよい。
- 一つの発話に複数の明示情報があれば、該当する複数項目を同時に更新してよい。
- 現在確認中の項目と無関係な過去情報を勝手に変更しない。
- 利用者が用語・選択肢・理由を質問した場合はturn_type=clarificationとする。検索だけ行い
  計算条件を確定できない場合もclarificationとし、updatesは空にする。
- assistant_messageには次の質問を書かない。次の質問はPython側が必ず一つだけ追加する。
- Web検索を使った場合、assistant_messageで「何を見つけたか」「どの面積定義か」
  「計算条件へ採用したか」を簡潔に伝える。出典リンクはアプリ側で別に表示する。
- Web検索で得た値でconfirmedをtrueにしない。最終的な条件確認は必ず利用者に求める。
- TEは来訪者数ではなく、発生と集中を足したトリップエンド数であると区別する。
- ホテルの1,300人TE/ha・日は参考値であり、宿泊者数・チェックイン人数とは呼ばない。
- 計算式、原単位、補正係数、結果を自分で計算しない。
- 「終了」「中止」など継続しない意思が明確ならturn_type=cancelとする。
- assistant_messageは簡潔な日本語にする。
""".strip()


STATE_TEMPLATE: dict[str, Any] = {
    "target_facility_name": None,
    "facility_types": [],
    "station_distance_m": None,
    "commercial": {
        "area_m2": None,
        "area_type": None,
        "day": None,
    },
    "office": {
        "area_m2": None,
        "location_key": None,
        "building_key": None,
        "commercial_floor_ratio": None,
    },
    "hotel": {
        "area_m2": None,
    },
    "apply_internal_trip_reduction": None,
    "confirmed": False,
    "web_research": {
        "summary": "",
        "queries": [],
        "sources": [],
        "derived_fields": [],
    },
}


QUESTIONS = {
    "facility_types": (
        "計算する用途、または対象施設の名前を教えてください。施設名だけでも、"
        "Webで公開情報を調べながら商業施設・オフィス・ホテルの条件を整理できます。"
    ),
    "station_distance_m": "最寄り鉄道駅から施設までの距離は何mですか？",
    "commercial.area_m2": "商業施設として算定する延床面積は何㎡ですか？",
    "commercial.area_type": (
        "商業施設の地域区分はどれですか？ a：三大都市圏中心部、"
        "b：三大都市圏郊外部・地方中枢都市、c：三大都市圏周辺部・地方都市です。"
    ),
    "commercial.day": "商業施設の対象日は平日と休日のどちらですか？",
    "office.area_m2": (
        "オフィスとして算定する延床面積は何㎡ですか？ "
        "商業床面積率が15%を超える場合は、事務所部分の面積を答えてください。"
    ),
    "office.location_key": "オフィスの地域区分は、a：都心部、b：周辺部のどちらですか？",
    "office.building_key": (
        "事務所ビルは、a：一般事務所ビル、b：単館型事務所ビルのどちらですか？"
    ),
    "office.commercial_floor_ratio": "事務所建物内の商業床面積率は何%ですか？",
    "hotel.area_m2": "ホテルとして算定する延床面積は何㎡ですか？",
    "apply_internal_trip_reduction": (
        "商業施設とオフィスの人TE合計に、参考値として内々交通5%低減を表示しますか？"
    ),
    "confirmation": "表示した条件で計算してよいですか？ 修正したい項目があれば自然文で教えてください。",
}


def new_state() -> dict[str, Any]:
    """空の対話状態を返す。"""
    return copy.deepcopy(STATE_TEMPLATE)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        cleaned = cleaned[first_newline + 1 :] if first_newline >= 0 else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM応答にJSONオブジェクトが含まれていません。")
        payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM応答JSONがオブジェクトではありません。")
    return payload


def _extract_grounding(candidate: dict[str, Any]) -> dict[str, list[Any]]:
    """Geminiの検索グラウンディング情報から安全な出典一覧を取り出す。"""
    metadata = candidate.get("groundingMetadata")
    if not isinstance(metadata, dict):
        return {"queries": [], "sources": []}

    queries = []
    for value in metadata.get("webSearchQueries", []):
        query = str(value or "").strip()
        if query and query not in queries:
            queries.append(query)

    sources = []
    seen_urls = set()
    for chunk in metadata.get("groundingChunks", []):
        web = chunk.get("web") if isinstance(chunk, dict) else None
        if not isinstance(web, dict):
            continue
        url = str(web.get("uri") or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append(
            {
                "title": str(web.get("title") or parsed.netloc).strip(),
                "url": url,
            }
        )
        if len(sources) >= 10:
            break
    return {"queries": queries[:10], "sources": sources}


def call_gemini(
    history: list[dict[str, str]],
    state: dict[str, Any],
    current_field: str,
    *,
    api_key: str,
    model: str,
    timeout: float,
) -> dict[str, Any]:
    """Geminiへ会話と現在状態を渡し、構造化応答を得る。"""
    if not api_key.strip():
        raise RuntimeError("GEMINI_API_KEYが設定されていません。")

    context = (
        "\n\n現在の入力状態:\n"
        + json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        + f"\n現在確認中の項目: {current_field}"
    )
    contents: list[dict[str, Any]] = []
    for item in history[-12:]:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        contents.append(
            {
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": content}],
            }
        )

    payload = {
        "system_instruction": {
            "parts": [{"text": SYSTEM_PROMPT + context}],
        },
        "contents": contents,
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    safe_model = urllib.parse.quote(model, safe="-._")
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{safe_model}:generateContent"
    )

    response_payload: dict[str, Any] | None = None
    for attempt in range(3):
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
                response_payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
                detail = str(error_payload.get("error", {}).get("message") or exc.reason)
            except (ValueError, AttributeError):
                detail = str(exc.reason)
            if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(0.5 * (2**attempt))
                continue
            raise RuntimeError(f"Gemini APIエラー ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(0.5 * (2**attempt))
                continue
            raise RuntimeError(f"Gemini APIに接続できません: {exc}") from exc

    if response_payload is None:
        raise RuntimeError("Gemini APIから応答を取得できませんでした。")
    candidates = response_payload.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Gemini応答にcandidateがありません。")
    candidate = candidates[0]
    content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
    parts = content.get("parts", []) if isinstance(content, dict) else []
    texts = [
        str(part.get("text"))
        for part in parts
        if isinstance(part, dict) and part.get("text")
    ]
    if not texts:
        raise ValueError("Gemini応答にテキストがありません。")
    result = _extract_json_object("\n".join(texts))
    grounding = _extract_grounding(candidate)
    if grounding["sources"] or grounding["queries"]:
        result["_grounding"] = grounding
    return result


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "はい", "する", "適用"}:
            return True
        if normalized in {"false", "no", "n", "いいえ", "しない", "不要"}:
            return False
    return None


def _nested_updates(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def apply_updates(state: dict[str, Any], updates: Any) -> list[str]:
    """LLMの更新値を型・範囲・列挙値で検査して状態へ反映する。"""
    if not isinstance(updates, dict):
        return []
    changed: list[str] = []

    target_name = str(updates.get("target_facility_name") or "").strip()
    if target_name:
        state["target_facility_name"] = target_name[:200]
        changed.append("target_facility_name")

    facilities = updates.get("facility_types")
    if isinstance(facilities, list):
        normalized = []
        for value in facilities:
            item = str(value).strip().lower()
            aliases = {
                "商業": "commercial",
                "商業施設": "commercial",
                "commercial": "commercial",
                "オフィス": "office",
                "office": "office",
                "ホテル": "hotel",
                "宿泊": "hotel",
                "hotel": "hotel",
            }
            canonical = aliases.get(item)
            if canonical and canonical not in normalized:
                normalized.append(canonical)
        if normalized:
            state["facility_types"] = normalized
            changed.append("facility_types")

    station_distance = _number(updates.get("station_distance_m"))
    if station_distance is not None and station_distance >= 0:
        state["station_distance_m"] = station_distance
        changed.append("station_distance_m")

    commercial_updates = _nested_updates(updates.get("commercial"))
    commercial = state["commercial"]
    area = _number(commercial_updates.get("area_m2"))
    if area is not None and area >= 0:
        commercial["area_m2"] = area
        changed.append("commercial.area_m2")
    area_type = str(commercial_updates.get("area_type") or "").strip().lower()
    if area_type in {"a", "b", "c"}:
        commercial["area_type"] = area_type
        changed.append("commercial.area_type")
    day = str(commercial_updates.get("day") or "").strip()
    if day in {"平日", "休日"}:
        commercial["day"] = day
        changed.append("commercial.day")

    office_updates = _nested_updates(updates.get("office"))
    office = state["office"]
    area = _number(office_updates.get("area_m2"))
    if area is not None and area >= 0:
        office["area_m2"] = area
        changed.append("office.area_m2")
    location_key = str(office_updates.get("location_key") or "").strip().lower()
    if location_key in {"a", "b"}:
        office["location_key"] = location_key
        changed.append("office.location_key")
    building_key = str(office_updates.get("building_key") or "").strip().lower()
    if building_key in {"a", "b"}:
        office["building_key"] = building_key
        changed.append("office.building_key")
    ratio = _number(office_updates.get("commercial_floor_ratio"))
    if ratio is not None and 0 <= ratio <= 100:
        office["commercial_floor_ratio"] = ratio
        changed.append("office.commercial_floor_ratio")

    hotel_updates = _nested_updates(updates.get("hotel"))
    hotel = state["hotel"]
    area = _number(hotel_updates.get("area_m2"))
    if area is not None and area >= 0:
        hotel["area_m2"] = area
        changed.append("hotel.area_m2")

    reduction = _boolean(updates.get("apply_internal_trip_reduction"))
    if reduction is not None:
        state["apply_internal_trip_reduction"] = reduction
        changed.append("apply_internal_trip_reduction")
    confirmed = _boolean(updates.get("confirmed"))
    if confirmed is not None:
        state["confirmed"] = confirmed
        changed.append("confirmed")
    return changed


def _web_derived_fields(response: dict[str, Any]) -> list[str]:
    research = response.get("web_research")
    if not isinstance(research, dict):
        return []
    allowed = {
        "facility_types",
        "station_distance_m",
        "commercial.area_m2",
        "commercial.area_type",
        "commercial.day",
        "office.area_m2",
        "office.location_key",
        "office.building_key",
        "office.commercial_floor_ratio",
        "hotel.area_m2",
    }
    values = research.get("derived_fields")
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value) in allowed]


def _remove_unverified_web_updates(response: dict[str, Any]) -> None:
    """検索出典がないWeb由来値が計算状態へ入ることを防ぐ。"""
    grounding = response.get("_grounding")
    sources = grounding.get("sources", []) if isinstance(grounding, dict) else []
    if sources:
        return
    updates = response.get("updates")
    if not isinstance(updates, dict):
        return
    for field in _web_derived_fields(response):
        if "." in field:
            parent, child = field.split(".", 1)
            nested = updates.get(parent)
            if isinstance(nested, dict):
                nested.pop(child, None)
        else:
            updates.pop(field, None)


def record_web_research(
    state: dict[str, Any],
    response: dict[str, Any],
    changed: list[str],
) -> None:
    """検索概要・クエリ・出典・Web由来フィールドを状態へ記録する。"""
    stored = state["web_research"]
    research = response.get("web_research")
    grounding = response.get("_grounding")
    grounding = grounding if isinstance(grounding, dict) else {}
    sources = grounding.get("sources", [])
    queries = grounding.get("queries", [])
    used = bool(sources or queries)
    if isinstance(research, dict):
        used = used or bool(research.get("used"))

    derived = _web_derived_fields(response) if sources else []
    if not used:
        stored["derived_fields"] = [
            field for field in stored["derived_fields"] if field not in changed
        ]
        return

    summary = str(research.get("summary") or "").strip() if isinstance(research, dict) else ""
    if summary:
        stored["summary"] = summary[:1000]

    for query in queries:
        value = str(query or "").strip()
        if value and value not in stored["queries"]:
            stored["queries"].append(value)
    stored["queries"] = stored["queries"][-10:]

    existing_urls = {item["url"] for item in stored["sources"]}
    for source in sources:
        if not isinstance(source, dict) or source.get("url") in existing_urls:
            continue
        stored["sources"].append(source)
        existing_urls.add(source["url"])
    stored["sources"] = stored["sources"][-10:]

    manual_fields = [field for field in changed if field not in derived]
    stored["derived_fields"] = [
        field for field in stored["derived_fields"] if field not in manual_fields
    ]
    for field in derived:
        if field in changed and field not in stored["derived_fields"]:
            stored["derived_fields"].append(field)


def next_required_field(state: dict[str, Any]) -> str:
    """選択用途に応じ、次に確認すべき項目を一つ返す。"""
    facilities = state["facility_types"]
    if not facilities:
        return "facility_types"
    if any(item in facilities for item in ("commercial", "office")):
        if state["station_distance_m"] is None:
            return "station_distance_m"
    if "commercial" in facilities:
        for key in ("area_m2", "area_type", "day"):
            if state["commercial"][key] is None:
                return f"commercial.{key}"
    if "office" in facilities:
        for key in ("area_m2", "location_key", "building_key", "commercial_floor_ratio"):
            if state["office"][key] is None:
                return f"office.{key}"
    if "hotel" in facilities and state["hotel"]["area_m2"] is None:
        return "hotel.area_m2"
    if {"commercial", "office"}.issubset(set(facilities)):
        if state["apply_internal_trip_reduction"] is None:
            return "apply_internal_trip_reduction"
    return "confirmation"


def format_condition_summary(state: dict[str, Any]) -> str:
    """計算前確認に使う入力条件を表示する。"""
    labels = {
        "commercial": "商業施設",
        "office": "オフィス",
        "hotel": "ホテル",
    }
    web_fields = set(state["web_research"]["derived_fields"])

    def origin(field: str) -> str:
        return "（Web検索）" if field in web_fields else ""

    lines = ["入力条件の確認"]
    if state.get("target_facility_name"):
        lines.append(f"- 対象施設: {state['target_facility_name']}")
    lines.append(
        f"- 用途: {', '.join(labels[x] for x in state['facility_types'])}"
        f"{origin('facility_types')}"
    )
    if any(x in state["facility_types"] for x in ("commercial", "office")):
        lines.append(
            f"- 駅距離: {state['station_distance_m']:g} m"
            f"{origin('station_distance_m')}"
        )
    if "commercial" in state["facility_types"]:
        value = state["commercial"]
        area_label = {"a": "三大都市圏中心部", "b": "三大都市圏郊外部・地方中枢都市", "c": "三大都市圏周辺部・地方都市"}
        lines.append(
            f"- 商業: {value['area_m2']:,.0f} ㎡{origin('commercial.area_m2')}、"
            f"{area_label[value['area_type']]}{origin('commercial.area_type')}、"
            f"{value['day']}{origin('commercial.day')}"
        )
    if "office" in state["facility_types"]:
        value = state["office"]
        location = {"a": "都心部", "b": "周辺部"}[value["location_key"]]
        building = {"a": "一般事務所ビル", "b": "単館型事務所ビル"}[value["building_key"]]
        lines.append(
            f"- オフィス: {value['area_m2']:,.0f} ㎡{origin('office.area_m2')}、"
            f"{location}{origin('office.location_key')}、"
            f"{building}{origin('office.building_key')}、"
            f"商業床{value['commercial_floor_ratio']:g}%"
            f"{origin('office.commercial_floor_ratio')}"
        )
    if "hotel" in state["facility_types"]:
        lines.append(
            f"- ホテル: {state['hotel']['area_m2']:,.0f} ㎡"
            f"{origin('hotel.area_m2')}"
        )
    if {"commercial", "office"}.issubset(set(state["facility_types"])):
        enabled = "表示する" if state["apply_internal_trip_reduction"] else "表示しない"
        lines.append(f"- 内々交通5%低減: {enabled}")
    return "\n".join(lines)


def calculate_from_state(state: dict[str, Any], calculations: Any) -> tuple[list[Any], float, float | None]:
    """確認済み状態を既存の決定論的計算関数へ渡す。"""
    results = []
    facilities = state["facility_types"]
    if "commercial" in facilities:
        value = state["commercial"]
        results.append(
            calculations.calculate_commercial(
                area_m2=value["area_m2"],
                area_type=value["area_type"],
                day=value["day"],
                station_distance_m=state["station_distance_m"],
            )
        )
    if "office" in facilities:
        value = state["office"]
        results.append(
            calculations.calculate_office(
                area_m2=value["area_m2"],
                location_key=value["location_key"],
                building_key=value["building_key"],
                commercial_floor_ratio=value["commercial_floor_ratio"],
                station_distance_m=state["station_distance_m"],
            )
        )
    if "hotel" in facilities:
        results.append(
            calculations.calculate_hotel(area_m2=state["hotel"]["area_m2"])
        )

    total = sum(result.trip_ends for result in results)
    adjusted_total = None
    if (
        {"commercial", "office"}.issubset(set(facilities))
        and state["apply_internal_trip_reduction"]
    ):
        by_name = {result.name: result for result in results}
        reduction = (by_name["商業施設"].trip_ends + by_name["オフィス"].trip_ends) * 0.05
        adjusted_total = total - reduction
    return results, total, adjusted_total


def _compose_reply(response: dict[str, Any], field: str, state: dict[str, Any]) -> str:
    message = str(response.get("assistant_message") or "").strip()
    parts = [message] if message else []
    if field == "confirmation":
        parts.append(format_condition_summary(state))
    parts.append(QUESTIONS[field])
    return "\n\n".join(parts)


def process_dialogue_turn(
    calculations: Any,
    state: dict[str, Any],
    history: list[dict[str, str]],
    user_text: str,
    *,
    api_key: str,
    model: str,
    timeout: float,
) -> dict[str, Any]:
    """端末・Web UIで共用する1ターン分の対話処理を行う。"""
    text = user_text.strip()
    if not text:
        raise ValueError("メッセージが空です。")
    if len(text) > 8000:
        raise ValueError("メッセージは8,000文字以内にしてください。")
    if text.lower() in {"終了", "中止", "やめる", "quit", "exit"}:
        return {
            "status": "cancelled",
            "reply": "対話を終了しました。",
            "state": state,
        }

    current_field = next_required_field(state)
    history.append({"role": "user", "content": text})
    try:
        response = call_gemini(
            history,
            state,
            current_field,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
    except Exception:
        history.pop()
        raise

    turn_type = str(response.get("turn_type") or "").strip().lower()
    if turn_type == "cancel":
        reply = str(response.get("assistant_message") or "対話を終了しました。").strip()
        history.append({"role": "assistant", "content": reply})
        return {
            "status": "cancelled",
            "reply": reply,
            "state": state,
        }

    _remove_unverified_web_updates(response)
    changed = [] if turn_type == "clarification" else apply_updates(
        state,
        response.get("updates"),
    )
    record_web_research(state, response, changed)
    if any(field != "confirmed" for field in changed):
        state["confirmed"] = False

    next_field = next_required_field(state)
    if next_field == "confirmation" and state["confirmed"]:
        results, total, adjusted_total = calculate_from_state(state, calculations)
        reply = "条件を確定しました。Pythonの計算関数で算定しました。"
        history.append({"role": "assistant", "content": reply})
        return {
            "status": "complete",
            "reply": reply,
            "state": state,
            "results": results,
            "total": total,
            "adjusted_total": adjusted_total,
        }

    reply = _compose_reply(response, next_field, state)
    history.append({"role": "assistant", "content": reply})
    return {
        "status": "continue",
        "reply": reply,
        "state": state,
    }


def run_llm_dialogue(
    calculations: Any,
    *,
    api_key: str,
    model: str,
    timeout: float,
) -> tuple[list[Any], float, float | None] | None:
    """端末上でGeminiとの一問一答を行い、確認後に計算する。"""
    state = new_state()
    history: list[dict[str, str]] = []
    print("=" * 72)
    print("LLM対話による 発生集中交通量計算")
    print("=" * 72)
    print("自然な日本語で回答できます。『終了』で中止します。")
    initial = QUESTIONS["facility_types"]
    print(f"\nアシスタント: {initial}")
    history.append({"role": "assistant", "content": initial})

    while True:
        try:
            user_text = input("\nあなた: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n対話を終了しました。")
            return None
        if not user_text:
            print("入力が空です。自然文で回答してください。")
            continue
        try:
            turn = process_dialogue_turn(
                calculations,
                state,
                history,
                user_text,
                api_key=api_key,
                model=model,
                timeout=timeout,
            )
        except Exception as exc:
            print(f"\nLLMとの通信に失敗しました: {exc}")
            print("同じ回答を再送するか、『終了』と入力してください。")
            continue
        if turn["status"] == "cancelled":
            print(f"\nアシスタント: {turn['reply']}")
            return None
        if turn["status"] == "complete":
            print(f"\nアシスタント: {turn['reply']}")
            return turn["results"], turn["total"], turn["adjusted_total"]
        print(f"\nアシスタント: {turn['reply']}")


def _api_key_from_environment_or_prompt() -> str:
    value = os.environ.get("GEMINI_API_KEY", "").strip()
    if value:
        return value
    if not os.isatty(0):
        return ""
    return getpass.getpass("Gemini APIキー（入力は表示されません）: ").strip()


def _print_final_results(
    calculations: Any,
    results: list[Any],
    total: float,
    adjusted_total: float | None,
) -> None:
    print("\n" + "=" * 72)
    print("計算結果")
    print("=" * 72)
    for result in results:
        calculations.print_result(result)
    print("\n" + "=" * 72)
    print(f"単純合計 : {total:,.0f} 人TE/日")
    if adjusted_total is not None:
        print(f"内々交通5%低減後合計 : {adjusted_total:,.0f} 人TE/日")
    print("\n注意")
    print("- TEは発生＋集中のトリップエンド数で、ユニーク来訪者数ではありません。")
    print("- ホテルの1,300人TE/ha・日はマニュアル上の参考値（標準偏差600）です。")


def run_cli(
    *,
    calculations: Any,
    manual_callback: Callable[[], None],
) -> None:
    """コマンドライン引数を処理してLLM対話または旧対話を起動する。"""
    parser = argparse.ArgumentParser(
        description="LLMとの対話から発生集中交通量の計算条件を収集します。"
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="LLMを使わず、従来の固定質問形式で実行します。",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        help=f"Geminiモデル名（既定: {DEFAULT_GEMINI_MODEL}）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Gemini APIのタイムアウト秒（既定: {DEFAULT_TIMEOUT:g}）",
    )
    parser.add_argument(
        "--output",
        default="facility_trip_estimation_result.csv",
        help="結果CSVの保存先",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="結果CSVを保存しません。",
    )
    args = parser.parse_args()

    if args.manual:
        manual_callback()
        return
    if args.timeout <= 0:
        parser.error("--timeoutは0より大きい値にしてください。")

    api_key = _api_key_from_environment_or_prompt()
    if not api_key:
        parser.error("GEMINI_API_KEYを環境変数へ設定してください。")

    calculated = run_llm_dialogue(
        calculations,
        api_key=api_key,
        model=args.model,
        timeout=args.timeout,
    )
    if calculated is None:
        return
    results, total, adjusted_total = calculated
    _print_final_results(calculations, results, total, adjusted_total)

    if not args.no_save:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        saved = calculations.save_csv(
            results,
            total,
            adjusted_total,
            path=str(output_path),
        )
        print(f"\nCSVを保存しました: {Path(saved).resolve()}")
