from __future__ import annotations

import geopandas as gpd
import pandas as pd
from pyproj import Transformer


WGS84 = "EPSG:4326"


def make_geodataframe(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Create a WGS84 GeoDataFrame from lon/lat columns."""
    if not {"lon", "lat"}.issubset(df.columns):
        raise ValueError("DataFrame must contain lon and lat columns.")
    geometry = gpd.points_from_xy(df["lon"], df["lat"], crs=WGS84)
    return gpd.GeoDataFrame(df.copy(), geometry=geometry, crs=WGS84)


def add_local_xy(
    df: pd.DataFrame,
    origin_lon: float,
    origin_lat: float,
    projected_crs: str = "EPSG:6677",
) -> pd.DataFrame:
    """Add local meter coordinates x/y relative to the configured origin."""
    transformer = Transformer.from_crs(WGS84, projected_crs, always_xy=True)
    xs, ys = transformer.transform(df["lon"].to_numpy(), df["lat"].to_numpy())
    origin_x, origin_y = transformer.transform(origin_lon, origin_lat)

    out = df.copy()
    out["x"] = xs - origin_x
    out["y"] = ys - origin_y
    return out


def distance_m_to_point(
    gdf: gpd.GeoDataFrame,
    lon: float,
    lat: float,
    projected_crs: str = "EPSG:6677",
) -> pd.Series:
    """Calculate point distances in meters to a lon/lat reference point."""
    projected = gdf.to_crs(projected_crs)
    point = gpd.GeoSeries(gpd.points_from_xy([lon], [lat], crs=WGS84)).to_crs(projected_crs).iloc[0]
    return projected.geometry.distance(point)
