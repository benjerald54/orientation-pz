"""Detect road-bounded city blocks and attach generation-ready buildings."""

from dataclasses import dataclass

from shapely.geometry import LineString, shape
from shapely.ops import polygonize, unary_union


def _tag_is_true(value):
    return str(value).lower() in {"yes", "true", "1"}


def _is_at_grade(properties):
    if _tag_is_true(properties.get("bridge", False)) or _tag_is_true(properties.get("tunnel", False)):
        return False
    try:
        return float(properties.get("layer", 0)) == 0
    except (TypeError, ValueError):
        return False


def _project_geometry(geometry, projection):
    def project(value):
        if isinstance(value, (list, tuple)):
            if (
                len(value) >= 2
                and isinstance(value[0], (int, float))
                and isinstance(value[1], (int, float))
            ):
                x, y = projection.forward(value[0], value[1])
                return [x, y, *value[2:]]
            return [project(child) for child in value]
        return value

    projected = dict(geometry)
    if "coordinates" in projected:
        projected["coordinates"] = project(projected["coordinates"])
    return shape(projected)


def _local_grid_summary(building_indices, road_edge_ids, features, road_network):
    counts = {}
    bearings = {}
    for feature_index in building_indices:
        properties = features[feature_index].get("properties") or {}
        grid_id = properties.get("local_grid_id")
        if grid_id is None:
            continue
        counts[grid_id] = counts.get(grid_id, 0) + 1
        bearings.setdefault(grid_id, properties.get("grid_bearing_degrees"))
    for edge_id in road_edge_ids:
        edge = road_network.edges[edge_id]
        grid_id = edge.properties.get("local_grid_id")
        if grid_id is None:
            continue
        counts[grid_id] = counts.get(grid_id, 0) + 1
        bearings.setdefault(grid_id, edge.properties.get("grid_bearing_degrees"))
    if not counts:
        return None
    selected_id = min(counts, key=lambda grid_id: (-counts[grid_id], grid_id))
    return {
        "local_grid_id": selected_id,
        "bearing_degrees": bearings.get(selected_id),
        "evidence_count": counts[selected_id],
    }


@dataclass(frozen=True)
class CityBlock:
    block_id: int
    boundary: tuple
    road_edges: tuple
    road_nodes: tuple
    buildings: tuple
    local_grid: dict | None
    metadata: dict

    def to_dict(self):
        return {
            "block_id": self.block_id,
            "boundary": [list(position) for position in self.boundary],
            "road_edges": list(self.road_edges),
            "road_nodes": list(self.road_nodes),
            "buildings": list(self.buildings),
            "local_grid": self.local_grid,
            "metadata": self.metadata,
        }


def detect_city_blocks(
    features,
    road_network,
    projection,
    generation_ready_feature_indices,
    relationships=(),
    minimum_area_meters2=100,
    road_boundary_tolerance_meters=1,
):
    """Polygonize enclosed at-grade roads and assign contained buildings."""
    eligible_roads = [
        edge for edge in road_network.edges if _is_at_grade(edge.properties)
    ]
    if not eligible_roads:
        return []

    road_geometries = {
        edge.edge_id: LineString([
            projection.forward(*edge.centerline[0]),
            projection.forward(*edge.centerline[-1]),
        ])
        for edge in eligible_roads
    }
    noded_linework = unary_union(list(road_geometries.values()))
    polygons = [
        polygon.normalize()
        for polygon in polygonize(noded_linework)
        if polygon.area >= minimum_area_meters2 and polygon.is_valid
    ]
    polygons.sort(key=lambda polygon: (
        polygon.bounds[0], polygon.bounds[1], polygon.bounds[2], polygon.bounds[3], polygon.area
    ))

    building_relationships = {
        relationship["source_feature_index"]: relationship
        for relationship in relationships
    }
    ready_buildings = []
    for feature_index in sorted(generation_ready_feature_indices):
        properties = features[feature_index].get("properties") or {}
        if properties.get("building") in (None, "no", "false", False):
            continue
        geometry = features[feature_index].get("geometry")
        if geometry is None:
            continue
        try:
            projected = _project_geometry(geometry, projection)
        except Exception:
            continue
        if not projected.is_empty and projected.is_valid:
            ready_buildings.append((feature_index, projected))

    blocks = []
    building_block_ids = {}
    for block_id, polygon in enumerate(polygons, start=1):
        member_buildings = []
        for feature_index, building in ready_buildings:
            anchor = building.representative_point()
            if polygon.covers(anchor):
                member_buildings.append(feature_index)
                building_block_ids.setdefault(feature_index, []).append(block_id)

        road_edge_ids = tuple(sorted(
            edge.edge_id for edge in eligible_roads
            if road_geometries[edge.edge_id].distance(polygon.boundary)
            <= road_boundary_tolerance_meters
        ))
        road_node_ids = tuple(sorted({
            node_id
            for edge_id in road_edge_ids
            for node_id in (road_network.edges[edge_id].start_node, road_network.edges[edge_id].end_node)
        }))
        road_classes = sorted({
            edge.properties.get("highway") or edge.properties.get("railway") or edge.properties.get("class")
            for edge in (road_network.edges[edge_id] for edge_id in road_edge_ids)
            if edge.properties.get("highway") or edge.properties.get("railway") or edge.properties.get("class")
        })
        local_grid = _local_grid_summary(
            member_buildings, road_edge_ids, features, road_network
        )
        boundary = tuple(
            projection.inverse(x, y) for x, y in polygon.exterior.coords
        )
        blocks.append(CityBlock(
            block_id=block_id,
            boundary=boundary,
            road_edges=road_edge_ids,
            road_nodes=road_node_ids,
            buildings=tuple(member_buildings),
            local_grid=local_grid,
            metadata={
                "area_meters2": round(polygon.area, 3),
                "perimeter_meters": round(polygon.length, 3),
                "building_count": len(member_buildings),
                "road_classes": road_classes,
                "building_road_context_count": sum(
                    feature_index in building_relationships for feature_index in member_buildings
                ),
            },
        ))

    return blocks
