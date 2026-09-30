#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path
import threading
import unittest
import urllib.request

import calculate_facility_trips as calculations
from age_distribution import build_age_distribution
from web_app import FacilityWebServer, csv_bytes


class FacilityWebAppTests(unittest.TestCase):
    def setUp(self):
        self.server = FacilityWebServer(
            ("127.0.0.1", 0),
            configured_api_key="",
            model="test-model",
            timeout=1,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request_json(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_index_and_configuration_are_served(self):
        with urllib.request.urlopen(self.base_url + "/", timeout=2) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("会話から条件を整理し", html)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")

        status, config = self.request_json("/api/config")
        self.assertEqual(status, 200)
        self.assertFalse(config["api_key_configured"])
        self.assertEqual(config["model"], "test-model")
        self.assertTrue(config["web_search_enabled"])

    def test_session_can_start_with_browser_supplied_key(self):
        status, session = self.request_json("/api/sessions", {"api_key": "test-key"})
        self.assertEqual(status, 201)
        self.assertEqual(session["status"], "active")
        self.assertIn("計算する用途", session["reply"])
        self.assertEqual(session["state"]["facility_types"], [])

    def test_six_scenario_distributions_are_served_without_llm(self):
        status, payload = self.request_json("/api/scenarios")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["scenarios"]), 6)
        self.assertEqual(payload["scenarios"][0]["name"], "商業中心型")
        self.assertEqual(payload["scenarios"][3]["name"], "全商業型")
        self.assertIn("age_gender_distribution", payload["scenarios"][0])
        self.assertEqual(
            payload["scenarios"][0]["allocation_percent"],
            {"commercial": 60, "office": 30, "hotel": 10},
        )
        self.assertEqual(payload["scenarios"][2]["name"], "ホテル中心型")
        self.assertEqual(
            payload["scenarios"][2]["reference_facility"],
            "セルリアンタワー",
        )
        self.assertEqual(len(payload["individual_chart_images"]), 6)
        self.assertIn(
            "age_gender_png",
            payload["individual_chart_images"]["commercial_centered"],
        )

    def test_csv_download_content_uses_existing_result(self):
        result = calculations.calculate_hotel(30000)
        age_distribution = build_age_distribution([result])
        payload = csv_bytes(
            [result],
            result.trip_ends,
            None,
            age_distribution,
            {
                "sources": [
                    {"title": "施設公式資料", "url": "https://example.com/source"}
                ]
            },
        ).decode("utf-8-sig")
        self.assertIn("ホテル参考値", payload)
        self.assertIn("単純合計_人TE日,3900.0", payload)
        self.assertIn("年齢性別別推定到着人トリップ", payload)
        self.assertIn("10歳未満,男性", payload)
        self.assertIn("推定到着量合計,全性別,1950,1950", payload)
        self.assertIn(
            "参照JSON,chukyo_pt_2022_age_gender_distribution_reference.json",
            payload,
        )
        self.assertIn("施設公式資料,https://example.com/source", payload)

    def test_static_assets_exist(self):
        root = Path(__file__).resolve().parent / "static"
        self.assertTrue((root / "index.html").is_file())
        self.assertTrue((root / "app.js").is_file())
        self.assertTrue((root / "style.css").is_file())


if __name__ == "__main__":
    unittest.main()
