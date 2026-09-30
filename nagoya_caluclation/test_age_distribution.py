#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

import calculate_facility_trips as calculations
from age_distribution import (
    AGE_GROUPS,
    GENDER_KEYS,
    allocate_integer,
    build_age_distribution,
    estimate_arrivals_from_trip_ends,
    joint_profile_percentages,
    load_distribution_reference,
    profile_percentages,
)


class AgeDistributionTests(unittest.TestCase):
    def test_profiles_sum_to_one(self):
        for key in ("commercial", "office", "hotel"):
            self.assertAlmostEqual(sum(profile_percentages(key).values()), 1.0)
            self.assertAlmostEqual(sum(joint_profile_percentages(key).values()), 1.0)

    def test_reference_age_totals_match_gender_totals(self):
        payload = load_distribution_reference()
        for ages in payload["facilities"].values():
            for values in ages.values():
                gender_total = sum(
                    values[f"{gender}_pct"] for gender in GENDER_KEYS
                )
                self.assertAlmostEqual(
                    gender_total,
                    values["age_total_pct"],
                    delta=0.0002,
                )

    def test_largest_remainder_allocation_preserves_total(self):
        allocated = allocate_integer(1_237, profile_percentages("commercial"))
        self.assertEqual(tuple(allocated), AGE_GROUPS)
        self.assertEqual(sum(allocated.values()), 1_237)

    def test_hotel_uses_half_of_trip_ends_as_arrivals(self):
        result = calculations.calculate_hotel(30_000)
        distribution = build_age_distribution([result])
        self.assertEqual(result.trip_ends, 3_900)
        self.assertEqual(distribution["estimated_arrivals_total"], 1_950)
        self.assertEqual(
            sum(group["count"] for group in distribution["combined_groups"]),
            1_950,
        )
        self.assertEqual(
            sum(gender["count"] for gender in distribution["genders"]),
            1_950,
        )
        for group in distribution["combined_groups"]:
            self.assertEqual(
                group["count"],
                sum(gender["count"] for gender in group["genders"]),
            )
        self.assertFalse(distribution["is_unique_people"])

    def test_multiple_facilities_preserve_combined_total(self):
        results = [
            calculations.calculate_hotel(30_000),
            calculations.calculate_office(
                area_m2=20_000,
                station_distance_m=300,
                location_key="a",
                building_key="a",
                commercial_floor_ratio=0,
            ),
        ]
        distribution = build_age_distribution(results)
        expected = sum(estimate_arrivals_from_trip_ends(item.trip_ends) for item in results)
        self.assertEqual(distribution["estimated_arrivals_total"], expected)
        self.assertEqual(
            sum(group["count"] for group in distribution["combined_groups"]),
            expected,
        )
        self.assertEqual(
            sum(gender["count"] for gender in distribution["genders"]),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
