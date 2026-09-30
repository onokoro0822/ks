from __future__ import annotations

from typing import Any

import geopandas as gpd
from shapely.geometry import Point, box


def filter_area(gdf: gpd.GeoDataFrame, area_config: dict[str, Any], projected_crs: str) -> gpd.GeoDataFrame:
    """Filter points by configured bbox or center-radius area."""
    area_type = area_config.get("type", "bbox")
    if area_type == "bbox":
        bbox_config = area_config["bbox"]
        geom = box(
            bbox_config["min_lon"],
            bbox_config["min_lat"],
            bbox_config["max_lon"],
            bbox_config["max_lat"],
        )
        return gdf[gdf.geometry.within(geom)].copy().reset_index(drop=True)

    if area_type == "center_radius":
        radius_config = area_config["center_radius"]
        center = gpd.GeoSeries(
            [Point(radius_config["center_lon"], radius_config["center_lat"])],
            crs=gdf.crs,
        ).to_crs(projected_crs).iloc[0]
        projected = gdf.to_crs(projected_crs)
        mask = projected.geometry.distance(center) <= float(radius_config["radius_m"])
        return gdf[mask].copy().reset_index(drop=True)

    raise ValueError(f"Unsupported area.type: {area_type}")
