#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import unittest
from unittest.mock import patch

import calculate_facility_trips as calculations
from llm_facility_dialogue import (
    apply_updates,
    call_gemini,
    calculate_from_state,
    new_state,
    next_required_field,
    process_dialogue_turn,
)


class LlmFacilityDialogueTests(unittest.TestCase):
    def test_gemini_json_response_is_parsed(self):
        llm_payload = {
            "assistant_message": "商業施設として承りました。",
            "turn_type": "answer",
            "updates": {"facility_types": ["commercial"]},
        }
        api_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps(llm_payload, ensure_ascii=False)}
                        ]
                    }
                }
            ]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(api_payload, ensure_ascii=False).encode("utf-8")

        with patch(
            "llm_facility_dialogue.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as mocked_urlopen:
            result = call_gemini(
                [
                    {"role": "assistant", "content": "用途を教えてください。"},
                    {"role": "user", "content": "商業施設です。"},
                ],
                new_state(),
                "facility_types",
                api_key="test-key",
                model="test-model",
                timeout=1,
            )

        self.assertEqual(result, llm_payload)
        request = mocked_urlopen.call_args.args[0]
        sent_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent_payload["tools"], [{"google_search": {}}])

    def test_grounded_search_updates_are_recorded_with_sources(self):
        state = new_state()
        history = [{"role": "assistant", "content": "対象施設を教えてください。"}]
        llm_response = {
            "assistant_message": "公式資料で延床面積を確認しました。",
            "turn_type": "answer",
            "updates": {
                "target_facility_name": "テスト施設",
                "facility_types": ["commercial"],
                "commercial": {"area_m2": 60_000},
            },
            "web_research": {
                "used": True,
                "summary": "公式資料に記載された延床面積を採用しました。",
                "derived_fields": ["commercial.area_m2"],
            },
            "_grounding": {
                "queries": ["テスト施設 延床面積"],
                "sources": [
                    {"title": "公式資料", "url": "https://example.com/facility"}
                ],
            },
        }
        with patch("llm_facility_dialogue.call_gemini", return_value=llm_response):
            turn = process_dialogue_turn(
                calculations,
                state,
                history,
                "テスト施設を調べて",
                api_key="test-key",
                model="test-model",
                timeout=1,
            )

        self.assertEqual(turn["status"], "continue")
        self.assertEqual(state["target_facility_name"], "テスト施設")
        self.assertEqual(state["commercial"]["area_m2"], 60_000)
        self.assertEqual(
            state["web_research"]["derived_fields"],
            ["commercial.area_m2"],
        )
        self.assertEqual(
            state["web_research"]["sources"][0]["url"],
            "https://example.com/facility",
        )

    def test_web_derived_value_without_grounding_is_not_applied(self):
        state = new_state()
        history = [{"role": "assistant", "content": "対象施設を教えてください。"}]
        llm_response = {
            "assistant_message": "床面積を見つけました。",
            "turn_type": "answer",
            "updates": {
                "facility_types": ["commercial"],
                "commercial": {"area_m2": 99_999},
            },
            "web_research": {
                "used": True,
                "summary": "出典なし",
                "derived_fields": ["commercial.area_m2"],
            },
        }
        with patch("llm_facility_dialogue.call_gemini", return_value=llm_response):
            process_dialogue_turn(
                calculations,
                state,
                history,
                "施設を調べて",
                api_key="test-key",
                model="test-model",
                timeout=1,
            )

        self.assertIsNone(state["commercial"]["area_m2"])

    def test_shared_turn_processor_completes_and_calculates(self):
        state = new_state()
        state.update(
            {
                "facility_types": ["hotel"],
                "hotel": {"area_m2": 30000.0},
                "confirmed": False,
            }
        )
        history = [{"role": "assistant", "content": "この条件で計算しますか？"}]
        llm_response = {
            "assistant_message": "確認しました。",
            "turn_type": "answer",
            "updates": {"confirmed": True},
        }
        with patch("llm_facility_dialogue.call_gemini", return_value=llm_response):
            turn = process_dialogue_turn(
                calculations,
                state,
                history,
                "はい、お願いします",
                api_key="test-key",
                model="test-model",
                timeout=1,
            )

        self.assertEqual(turn["status"], "complete")
        self.assertEqual(turn["total"], 3900.0)
        self.assertEqual(turn["results"][0].name, "ホテル")

    def test_questions_follow_only_selected_facilities(self):
        state = new_state()
        self.assertEqual(next_required_field(state), "facility_types")

        apply_updates(state, {"facility_types": ["commercial"]})
        self.assertEqual(next_required_field(state), "station_distance_m")
        apply_updates(state, {"station_distance_m": 100})
        self.assertEqual(next_required_field(state), "commercial.area_m2")

        apply_updates(
            state,
            {
                "commercial": {
                    "area_m2": 60000,
                    "area_type": "b",
                    "day": "平日",
                }
            },
        )
        self.assertEqual(next_required_field(state), "confirmation")

    def test_invalid_llm_values_are_rejected(self):
        state = new_state()
        changed = apply_updates(
            state,
            {
                "station_distance_m": -1,
                "commercial": {"area_type": "z"},
                "office": {"commercial_floor_ratio": 101},
            },
        )
        self.assertEqual(changed, [])
        self.assertIsNone(state["station_distance_m"])
        self.assertIsNone(state["commercial"]["area_type"])
        self.assertIsNone(state["office"]["commercial_floor_ratio"])

    def test_structured_state_uses_existing_calculation_functions(self):
        state = new_state()
        apply_updates(
            state,
            {
                "facility_types": ["commercial", "office", "hotel"],
                "station_distance_m": 100,
                "commercial": {
                    "area_m2": 60000,
                    "area_type": "b",
                    "day": "平日",
                },
                "office": {
                    "area_m2": 60000,
                    "location_key": "a",
                    "building_key": "a",
                    "commercial_floor_ratio": 0,
                },
                "hotel": {"area_m2": 30000},
                "apply_internal_trip_reduction": True,
            },
        )
        results, total, adjusted = calculate_from_state(state, calculations)

        expected = [
            calculations.calculate_commercial(60000, "b", "平日", 100),
            calculations.calculate_office(60000, "a", "a", 0, 100),
            calculations.calculate_hotel(30000),
        ]
        self.assertEqual([r.trip_ends for r in results], [r.trip_ends for r in expected])
        self.assertEqual(total, sum(r.trip_ends for r in expected))
        reduction = (expected[0].trip_ends + expected[1].trip_ends) * 0.05
        self.assertEqual(adjusted, total - reduction)


if __name__ == "__main__":
    unittest.main()
