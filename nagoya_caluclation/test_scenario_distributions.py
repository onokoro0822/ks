#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from scenario_distributions import (
    build_scenario_distributions,
    generate_distributions,
    load_scenarios,
    save_demand_chart,
    save_individual_scenario_charts,
    save_scenario_chart,
)


class ScenarioDistributionTests(unittest.TestCase):
    def setUp(self):
        self.payload = load_scenarios()
        self.result = build_scenario_distributions(self.payload)

    def test_six_former_department_store_scenarios_and_areas_are_loaded(self):
        scenarios = {item["id"]: item for item in self.payload["scenarios"]}
        self.assertEqual(len(scenarios), 6)
        self.assertEqual(
            scenarios["commercial_centered"]["allocation_percent"],
            {"commercial": 60, "office": 30, "hotel": 10},
        )
        self.assertEqual(
            scenarios["commercial_centered"]["floor_area_m2"],
            {"commercial": 32_625, "office": 16_312, "hotel": 5_437},
        )
        self.assertEqual(
            scenarios["commercial_centered"]["reference_facility"],
            "JRゲートタワー",
        )
        self.assertEqual(
            scenarios["office_centered"]["allocation_percent"],
            {"commercial": 5, "office": 80, "hotel": 15},
        )
        self.assertEqual(
            scenarios["office_centered"]["floor_area_m2"],
            {"commercial": 2_719, "office": 43_499, "hotel": 8_156},
        )
        self.assertEqual(
            scenarios["hotel_centered"]["allocation_percent"],
            {"commercial": 20, "office": 25, "hotel": 55},
        )
        self.assertEqual(
            scenarios["hotel_centered"]["floor_area_m2"],
            {"commercial": 10_875, "office": 13_593, "hotel": 29_906},
        )
        self.assertEqual(
            scenarios["all_commercial"]["floor_area_m2"],
            {"commercial": 54_374, "office": 0, "hotel": 0},
        )
        self.assertEqual(
            scenarios["all_office"]["floor_area_m2"],
            {"commercial": 0, "office": 54_374, "hotel": 0},
        )
        self.assertEqual(
            scenarios["all_hotel"]["floor_area_m2"],
            {"commercial": 0, "office": 0, "hotel": 54_374},
        )

    def test_each_scenario_uses_the_full_former_store_reference_area(self):
        reference = self.payload["planning_scale_reference"]
        self.assertEqual(reference["former_department_store_sales_area_m2"], 54_374)
        self.assertEqual(reference["scenario_total_area_m2"], 54_374)
        for scenario in self.payload["scenarios"]:
            self.assertEqual(sum(scenario["floor_area_m2"].values()), 54_374)
            self.assertEqual(sum(scenario["allocation_percent"].values()), 100)

    def test_meitetsu_nagoya_commercial_area_type_is_category_b(self):
        commercial = self.payload["calculation_conditions"]["commercial"]
        self.assertEqual(commercial["area_type"], "b")
        self.assertEqual(
            commercial["area_type_label"],
            "三大都市圏郊外部および地方中枢都市",
        )

    def test_each_distribution_preserves_estimated_arrival_total(self):
        for scenario in self.result["scenarios"]:
            distribution = scenario["age_gender_distribution"]
            self.assertEqual(
                sum(group["count"] for group in distribution["combined_groups"]),
                distribution["estimated_arrivals_total"],
            )
            self.assertEqual(
                sum(gender["count"] for gender in distribution["genders"]),
                distribution["estimated_arrivals_total"],
            )

    def test_dominant_facility_area_changes_facility_trip_ends(self):
        scenarios = {item["id"]: item for item in self.result["scenarios"]}
        commercial = {
            item["key"]: item
            for item in scenarios["commercial_centered"]["facility_results"]
        }
        office = {
            item["key"]: item
            for item in scenarios["office_centered"]["facility_results"]
        }
        self.assertGreater(
            commercial["commercial"]["trip_ends"],
            office["commercial"]["trip_ends"],
        )
        self.assertGreater(
            office["office"]["trip_ends"],
            commercial["office"]["trip_ends"],
        )

    def test_single_use_scenarios_do_not_apply_internal_trip_reduction(self):
        scenarios = {item["id"]: item for item in self.result["scenarios"]}
        for scenario_id in ("all_commercial", "all_office", "all_hotel"):
            scenario = scenarios[scenario_id]
            self.assertEqual(scenario["internal_trip_reduction"], 0)
            self.assertEqual(
                scenario["adjusted_total_trip_ends"],
                scenario["total_trip_ends"],
            )

    def test_zero_area_facilities_have_zero_arrivals(self):
        scenarios = {item["id"]: item for item in self.result["scenarios"]}
        for scenario_id, active_key in (
            ("all_commercial", "commercial"),
            ("all_office", "office"),
            ("all_hotel", "hotel"),
        ):
            facilities = {
                item["key"]: item
                for item in scenarios[scenario_id]["age_gender_distribution"]["facilities"]
            }
            self.assertGreater(facilities[active_key]["estimated_arrivals"], 0)
            for facility_key, facility in facilities.items():
                if facility_key != active_key:
                    self.assertEqual(facility["estimated_arrivals"], 0)

    def test_publication_charts_can_be_saved_as_dependency_free_svg(self):
        with TemporaryDirectory() as directory:
            age_output = Path(directory) / "age_gender.svg"
            demand_output = Path(directory) / "demand.svg"
            self.assertEqual(
                save_scenario_chart(self.result, age_output),
                age_output.resolve(),
            )
            self.assertEqual(
                save_demand_chart(self.result, demand_output),
                demand_output.resolve(),
            )
            age_content = age_output.read_text(encoding="utf-8")
            demand_content = demand_output.read_text(encoding="utf-8")
            self.assertIn("<svg", age_content)
            self.assertIn("Age–gender composition", age_content)
            self.assertIn("All commercial", age_content)
            self.assertIn("trips/day", age_content)
            self.assertIn("%", age_content)
            self.assertIn("Estimated daily arrivals", demand_content)
            self.assertIn("Commercial-centered", demand_content)

    def test_each_scenario_chart_is_saved_as_an_individual_png_and_svg(self):
        with TemporaryDirectory() as directory:
            output_dir = Path(directory) / "scenario_charts"
            stale_png = output_dir / "03_balanced_age_gender_en.png"
            stale_svg = output_dir / "03_balanced_age_gender_en.svg"
            output_dir.mkdir(parents=True)
            stale_png.write_bytes(b"old")
            stale_svg.write_text("old", encoding="utf-8")
            saved = save_individual_scenario_charts(self.result, output_dir)
            self.assertEqual(set(saved), {item["id"] for item in self.result["scenarios"]})
            for scenario_id, chart_paths in saved.items():
                self.assertEqual(
                    set(chart_paths),
                    {
                        "age_gender_png",
                        "age_gender_svg",
                        "arrivals_by_use_png",
                        "arrivals_by_use_svg",
                    },
                )
                for path in chart_paths.values():
                    self.assertTrue(path.is_file(), f"{scenario_id}: {path}")
                self.assertGreater(chart_paths["age_gender_png"].stat().st_size, 1_000)
                self.assertGreater(chart_paths["arrivals_by_use_png"].stat().st_size, 1_000)
                self.assertIn(
                    scenario_id,
                    chart_paths["age_gender_png"].name,
                )
            self.assertFalse(stale_png.exists())
            self.assertFalse(stale_svg.exists())
            hotel_svg = saved["hotel_centered"]["age_gender_svg"].read_text(
                encoding="utf-8"
            )
            self.assertIn("Reference facility: Cerulean Tower", hotel_svg)
            self.assertIn("Estimated arrival trips/day by age and gender", hotel_svg)
            first_group = next(
                scenario
                for scenario in self.result["scenarios"]
                if scenario["id"] == "hotel_centered"
            )["age_gender_distribution"]["combined_groups"][0]
            self.assertIn(f">n={first_group['count']:,}<", hotel_svg)

    def test_history_is_created_only_when_input_content_changes(self):
        source_dir = Path(__file__).resolve().parent
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "nagoya_three_development_scenarios.json"
            reference_path = root / "chukyo_pt_2022_age_gender_distribution_reference.json"
            shutil.copy2(source_dir / input_path.name, input_path)
            shutil.copy2(source_dir / reference_path.name, reference_path)
            output_path = root / "result.json"
            history_dir = root / "history"

            first = generate_distributions(
                input_path,
                output_path,
                None,
                None,
                None,
                history_dir,
            )
            self.assertTrue(first["generation"]["history_created"])
            self.assertEqual(len(list(history_dir.glob("*/manifest.json"))), 1)
            self.assertEqual(input_path.read_bytes(), (source_dir / input_path.name).read_bytes())

            second = generate_distributions(
                input_path,
                output_path,
                None,
                None,
                None,
                history_dir,
            )
            self.assertFalse(second["generation"]["history_created"])
            self.assertEqual(len(list(history_dir.glob("*/manifest.json"))), 1)


if __name__ == "__main__":
    unittest.main()
