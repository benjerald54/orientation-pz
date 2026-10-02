"""Relate generation-ready building footprints to their surrounding roads."""

import math
from dataclasses import dataclass

from shapely.geometry import LineString, MultiLineString, shape
from shapely.ops import nearest_points
from shapely.strtree import STRtree


def _project_value(value, projection):
    if isinstance(value, (list, tuple)):
        if (
            len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            x, y = projection.forward(value[0], value[1])
            return [x, y, *value[2:]]
        return [_project_value(child, projection) for child in value]
    return value


def _project_geometry(geometry, projection):
    projected = dict(geometry)
    if "coordinates" in projected:
        projected["coordinates"] = _project_value(projected["coordinates"], projection)
    if "geometries" in projected:
        projected["geometries"] = [
            _project_geometry(child, projection) for child in projected["geometries"]
        ]
    return projected


def _building_exterior(geometry, projection):
    projected = shape(_project_geometry(geometry, projection))
    if projected.geom_type == "Polygon":
        return LineString(projected.exterior.coords)
    if projected.geom_type == "MultiPolygon":
        exteriors = [LineString(polygon.exterior.coords) for polygon in projected.geoms]
        return MultiLineString(exteriors)
    return None


def _road_class(properties):
    return (
        properties.get("highway")
        or properties.get("railway")
        or properties.get("class")
    )


def _cardinal_direction(bearing_degrees):
    directions = ("east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast")
    return directions[int((bearing_degrees + 22.5) // 45) % len(directions)]


def _local_grid_side(dx, dy, bearing_degrees):
    if bearing_degrees is None:
        return None
    angle = math.radians(bearing_degrees)
    along_axis = dx * math.cos(angle) + dy * math.sin(angle)
    across_axis = -dx * math.sin(angle) + dy * math.cos(angle)
    if abs(along_axis) >= abs(across_axis):
        return "grid_axis_positive" if along_axis >= 0 else "grid_axis_negative"
    return "cross_axis_positive" if across_axis >= 0 else "cross_axis_negative"


@dataclass(frozen=True)
class BuildingRoadRelationship:
    source_feature_index: int
    nearest_road: dict | None
    nearest_point_on_building: tuple | None
    nearest_point_on_road: tuple | None
    distance_to_road_meters: float | None
    approach_direction: dict | None
    adjacent_roads: tuple
    local_grid_id: int | None
    local_grid_bearing_degrees: float | None

    def to_dict(self):
        return {
            "source_feature_index": self.source_feature_index,
            "nearest_road": self.nearest_road,
            "nearest_point_on_building": list(self.nearest_point_on_building)
            if self.nearest_point_on_building is not None else None,
            "nearest_point_on_road": list(self.nearest_point_on_road)
            if self.nearest_point_on_road is not None else None,
            "distance_to_road_meters": round(self.distance_to_road_meters, 3)
            if self.distance_to_road_meters is not None else None,
            "approach_direction": self.approach_direction,
            "adjacent_roads": list(self.adjacent_roads),
            "local_grid_id": self.local_grid_id,
            "local_grid_bearing_degrees": self.local_grid_bearing_degrees,
        }


def build_building_road_relationships(
    features,
    road_network,
    projection,
    generation_ready_feature_indices,
    adjacency_distance_meters=40,
):
    """Find each eligible building's nearest road and unique adjacent roads."""
    road_lines = []
    edge_records = []
    for edge in road_network.edges:
        line = LineString([
            projection.forward(*edge.centerline[0]),
            projection.forward(*edge.centerline[-1]),
        ])
        road_lines.append(line)
        edge_records.append(edge)
    road_index = STRtree(road_lines) if road_lines else None
    relationships = []
    for feature_index in sorted(generation_ready_feature_indices):
        feature = features[feature_index]
        properties = feature.get("properties") or {}
        if properties.get("building") in (None, "no", "false", False):
            continue
        geometry = feature.get("geometry")
        if geometry is None or road_index is None:
            relationships.append(BuildingRoadRelationship(
                source_feature_index=feature_index,
                nearest_road=None,
                nearest_point_on_building=None,
                nearest_point_on_road=None,
                distance_to_road_meters=None,
                approach_direction=None,
                adjacent_roads=(),
                local_grid_id=properties.get("local_grid_id"),
                local_grid_bearing_degrees=properties.get("grid_bearing_degrees"),
            ))
            continue

        try:
            exterior = _building_exterior(geometry, projection)
        except Exception:
            exterior = None
        if exterior is None or exterior.is_empty:
            continue

        nearby_indices = {
            int(index)
            for index in road_index.query(exterior.buffer(adjacency_distance_meters))
        }
        nearest_index = int(road_index.nearest(exterior))
        candidate_indices = nearby_indices | {nearest_index}
        per_road = {}
        for edge_index in candidate_indices:
            edge = edge_records[edge_index]
            road_line = road_lines[edge_index]
            building_point, road_point = nearest_points(exterior, road_line)
            distance = building_point.distance(road_point)
            record = {
                "source_feature_index": edge.source_feature_index,
                "source_feature_id": edge.source_feature_id,
                "edge_id": edge.edge_id,
                "road_class": _road_class(edge.properties),
                "distance_meters": distance,
                "building_point": (building_point.x, building_point.y),
                "road_point": (road_point.x, road_point.y),
            }
            road_key = edge.source_feature_index
            existing = per_road.get(road_key)
            if existing is None or (distance, edge.edge_id) < (
                existing["distance_meters"], existing["edge_id"]
            ):
                per_road[road_key] = record

        roads_by_distance = sorted(
            per_road.values(),
            key=lambda record: (
                record["distance_meters"],
                record["source_feature_index"],
                record["edge_id"],
            ),
        )
        nearest = roads_by_distance[0]
        adjacent = [
            record for record in roads_by_distance
            if record["distance_meters"] <= adjacency_distance_meters
        ]
        dx = nearest["road_point"][0] - nearest["building_point"][0]
        dy = nearest["road_point"][1] - nearest["building_point"][1]
        bearing = math.degrees(math.atan2(dy, dx)) % 360 if math.hypot(dx, dy) > 1e-9 else None
        grid_bearing = properties.get("grid_bearing_degrees")
        road_payload = {
            key: nearest[key]
            for key in ("source_feature_index", "source_feature_id", "edge_id", "road_class")
        }
        adjacent_payload = tuple(
            {
                key: record[key]
                for key in ("source_feature_index", "source_feature_id", "edge_id", "road_class")
            } | {"distance_meters": round(record["distance_meters"], 3)}
            for record in adjacent
        )
        approach = None if bearing is None else {
            "bearing_degrees": round(bearing, 3),
            "cardinal_direction": _cardinal_direction(bearing),
            "vector_meters": [round(dx, 3), round(dy, 3)],
            "local_grid_side": _local_grid_side(dx, dy, grid_bearing),
        }
        relationships.append(BuildingRoadRelationship(
            source_feature_index=feature_index,
            nearest_road=road_payload,
            nearest_point_on_building=projection.inverse(*nearest["building_point"]),
            nearest_point_on_road=projection.inverse(*nearest["road_point"]),
            distance_to_road_meters=nearest["distance_meters"],
            approach_direction=approach,
            adjacent_roads=adjacent_payload,
            local_grid_id=properties.get("local_grid_id"),
            local_grid_bearing_degrees=grid_bearing,
        ))
    return relationships