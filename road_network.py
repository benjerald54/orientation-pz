"""Extract normalized road centerlines and build a noded road graph."""

import math
from collections import defaultdict
from dataclasses import dataclass, field

from shapely.geometry import LineString
from shapely.strtree import STRtree


@dataclass(frozen=True)
class RoadFeature:
    source_feature_index: int
    source_feature_id: object
    properties: dict
    connectivity_key: tuple
    centerlines: tuple


@dataclass
class RoadNode:
    node_id: int
    x: float
    y: float
    coordinate: tuple
    connectivity_key: tuple
    edge_ids: list = field(default_factory=list)

    @property
    def degree(self):
        return len(self.edge_ids)


@dataclass(frozen=True)
class RoadEdge:
    edge_id: int
    start_node: int
    end_node: int
    source_feature_index: int
    source_feature_id: object
    centerline: tuple
    length_meters: float
    properties: dict


@dataclass
class RoadNetwork:
    nodes: list
    edges: list
    source_road_count: int
    noded_intersection_count: int

    def connected_components(self):
        adjacency = {node.node_id: set() for node in self.nodes}
        for edge in self.edges:
            adjacency[edge.start_node].add(edge.end_node)
            adjacency[edge.end_node].add(edge.start_node)

        components = []
        visited = set()
        for start in sorted(adjacency):
            if start in visited:
                continue
            component = set()
            pending = [start]
            while pending:
                node = pending.pop()
                if node in visited:
                    continue
                visited.add(node)
                component.add(node)
                pending.extend(adjacency[node] - visited)
            components.append(sorted(component))
        return components

    def to_dict(self):
        return {
            "nodes": [
                {
                    "id": node.node_id,
                    "coordinate": list(node.coordinate),
                    "degree": node.degree,
                    "edge_ids": sorted(node.edge_ids),
                    "connectivity_key": list(node.connectivity_key),
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "id": edge.edge_id,
                    "start_node": edge.start_node,
                    "end_node": edge.end_node,
                    "source_feature_index": edge.source_feature_index,
                    "source_feature_id": edge.source_feature_id,
                    "centerline": [list(position) for position in edge.centerline],
                    "length_meters": edge.length_meters,
                    "properties": edge.properties,
                }
                for edge in self.edges
            ],
            "summary": {
                "source_road_count": self.source_road_count,
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "connected_component_count": len(self.connected_components()),
                "noded_intersection_count": self.noded_intersection_count,
            },
        }


@dataclass
class _Segment:
    feature: RoadFeature
    centerline_index: int
    segment_index: int
    start: tuple
    end: tuple
    geometry: LineString
    split_points: list


def _is_position(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    )


def _iter_lines(geometry):
    if not geometry:
        return
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "LineString":
        yield coordinates
    elif geometry_type == "MultiLineString":
        yield from coordinates
    elif geometry_type == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from _iter_lines(child)


def _tag_is_true(value):
    return str(value).lower() in {"yes", "true", "1"}


def _connectivity_key(properties):
    layer = properties.get("layer", 0)
    try:
        layer = float(layer)
    except (TypeError, ValueError):
        layer = str(layer)
    return layer, _tag_is_true(properties.get("bridge", False)), _tag_is_true(properties.get("tunnel", False))


def _normalize_centerline(line, projection, duplicate_tolerance_meters):
    normalized = []
    previous_metric = None
    for position in line:
        if not _is_position(position):
            continue
        metric = projection.forward(position[0], position[1])
        if previous_metric is not None and math.hypot(
            metric[0] - previous_metric[0], metric[1] - previous_metric[1]
        ) <= duplicate_tolerance_meters:
            continue
        normalized.append(tuple(position))
        previous_metric = metric
    return tuple(normalized) if len(normalized) >= 2 else ()


def extract_road_features(features, projection, source_feature_indices=None, duplicate_tolerance_meters=1e-6):
    """Select transport centerlines and normalize consecutive duplicate points."""
    indices = range(len(features)) if source_feature_indices is None else source_feature_indices
    roads = []
    for feature_index in indices:
        feature = features[feature_index]
        properties = dict(feature.get("properties") or {})
        if not properties.get("highway") and not properties.get("railway"):
            continue
        centerlines = tuple(
            normalized
            for line in _iter_lines(feature.get("geometry"))
            if (normalized := _normalize_centerline(line, projection, duplicate_tolerance_meters))
        )
        if not centerlines:
            continue
        roads.append(RoadFeature(
            source_feature_index=feature_index,
            source_feature_id=feature.get("id"),
            properties=properties,
            connectivity_key=_connectivity_key(properties),
            centerlines=centerlines,
        ))
    return roads


def _intersection_points(geometry):
    if geometry.is_empty:
        return
    if geometry.geom_type == "Point":
        yield (geometry.x, geometry.y)
    elif geometry.geom_type in {"MultiPoint", "GeometryCollection"}:
        for child in geometry.geoms:
            yield from _intersection_points(child)
    elif geometry.geom_type == "LineString":
        coordinates = list(geometry.coords)
        if coordinates:
            yield coordinates[0][:2]
            yield coordinates[-1][:2]
    elif geometry.geom_type == "MultiLineString":
        for child in geometry.geoms:
            yield from _intersection_points(child)


def _find(parent, item):
    while parent[item] != item:
        parent[item] = parent[parent[item]]
        item = parent[item]
    return item


def _union(parent, first, second):
    first_root = _find(parent, first)
    second_root = _find(parent, second)
    if first_root != second_root:
        parent[max(first_root, second_root)] = min(first_root, second_root)


def build_road_network(road_features, projection, node_tolerance_meters=0.05):
    """Node centerlines and return a deterministic graph with grade separation."""
    if node_tolerance_meters < 0:
        raise ValueError("Node tolerance must not be negative")

    segments = []
    for road in road_features:
        for centerline_index, centerline in enumerate(road.centerlines):
            metric = [projection.forward(position[0], position[1]) for position in centerline]
            for segment_index, (start, end) in enumerate(zip(metric, metric[1:])):
                if math.hypot(end[0] - start[0], end[1] - start[1]) <= 1e-9:
                    continue
                segments.append(_Segment(
                    feature=road,
                    centerline_index=centerline_index,
                    segment_index=segment_index,
                    start=start,
                    end=end,
                    geometry=LineString((start, end)),
                    split_points=[(0.0, start), (1.0, end)],
                ))

    if not segments:
        return RoadNetwork([], [], len(road_features), 0)

    tree = STRtree([segment.geometry for segment in segments])
    intersection_coordinates = set()
    for first_index, segment in enumerate(segments):
        for matched in tree.query(segment.geometry):
            second_index = int(matched)
            if second_index <= first_index:
                continue
            other = segments[second_index]
            if segment.feature.connectivity_key != other.feature.connectivity_key:
                continue
            intersection = segment.geometry.intersection(other.geometry)
            points = sorted(set(_intersection_points(intersection)))
            for point in points:
                first_length = math.hypot(segment.end[0] - segment.start[0], segment.end[1] - segment.start[1])
                second_length = math.hypot(other.end[0] - other.start[0], other.end[1] - other.start[1])
                first_parameter = (
                    (point[0] - segment.start[0]) * (segment.end[0] - segment.start[0])
                    + (point[1] - segment.start[1]) * (segment.end[1] - segment.start[1])
                ) / (first_length * first_length)
                second_parameter = (
                    (point[0] - other.start[0]) * (other.end[0] - other.start[0])
                    + (point[1] - other.start[1]) * (other.end[1] - other.start[1])
                ) / (second_length * second_length)
                segment.split_points.append((first_parameter, point))
                other.split_points.append((second_parameter, point))
                first_is_interior = 1e-9 < first_parameter < 1 - 1e-9
                second_is_interior = 1e-9 < second_parameter < 1 - 1e-9
                if first_is_interior or second_is_interior:
                    intersection_coordinates.add((segment.feature.connectivity_key, point))

    occurrences = []
    segment_occurrences = []
    for segment_index, segment in enumerate(segments):
        unique_points = {}
        for parameter, point in segment.split_points:
            unique_points[(round(parameter, 12), point)] = (parameter, point)
        ordered = sorted(unique_points.values(), key=lambda item: (item[0], item[1]))
        occurrence_ids = []
        for _, point in ordered:
            occurrence_ids.append(len(occurrences))
            occurrences.append((point, segment.feature.connectivity_key))
        segment_occurrences.append((segment_index, ordered, occurrence_ids))

    parent = list(range(len(occurrences)))
    if node_tolerance_meters > 0:
        cell_size = node_tolerance_meters
        buckets = defaultdict(list)
        for occurrence_index, (point, connectivity_key) in enumerate(occurrences):
            cell_x = math.floor(point[0] / cell_size)
            cell_y = math.floor(point[1] / cell_size)
            for offset_x in (-1, 0, 1):
                for offset_y in (-1, 0, 1):
                    for candidate in buckets[(cell_x + offset_x, cell_y + offset_y, connectivity_key)]:
                        other_point = occurrences[candidate][0]
                        if math.hypot(point[0] - other_point[0], point[1] - other_point[1]) <= node_tolerance_meters:
                            _union(parent, occurrence_index, candidate)
            buckets[(cell_x, cell_y, connectivity_key)].append(occurrence_index)
    else:
        exact_occurrences = {}
        for occurrence_index, (point, connectivity_key) in enumerate(occurrences):
            key = (connectivity_key, point)
            if key in exact_occurrences:
                _union(parent, occurrence_index, exact_occurrences[key])
            else:
                exact_occurrences[key] = occurrence_index

    clusters = defaultdict(list)
    for occurrence_index in range(len(occurrences)):
        clusters[(occurrences[occurrence_index][1], _find(parent, occurrence_index))].append(occurrence_index)
    ordered_clusters = sorted(
        clusters.items(),
        key=lambda item: (
            repr(item[0][0]),
            sum(occurrences[index][0][0] for index in item[1]) / len(item[1]),
            sum(occurrences[index][0][1] for index in item[1]) / len(item[1]),
            min(item[1]),
        ),
    )

    nodes = []
    occurrence_to_node = {}
    for node_id, ((connectivity_key, _), member_indices) in enumerate(ordered_clusters):
        x = sum(occurrences[index][0][0] for index in member_indices) / len(member_indices)
        y = sum(occurrences[index][0][1] for index in member_indices) / len(member_indices)
        nodes.append(RoadNode(node_id, x, y, projection.inverse(x, y), connectivity_key))
        for occurrence_index in member_indices:
            occurrence_to_node[occurrence_index] = node_id

    edges = []
    for segment_index, ordered, occurrence_ids in segment_occurrences:
        segment = segments[segment_index]
        for point_index in range(len(ordered) - 1):
            start_node = occurrence_to_node[occurrence_ids[point_index]]
            end_node = occurrence_to_node[occurrence_ids[point_index + 1]]
            if start_node == end_node:
                continue
            start_x, start_y = nodes[start_node].x, nodes[start_node].y
            end_x, end_y = nodes[end_node].x, nodes[end_node].y
            length = math.hypot(end_x - start_x, end_y - start_y)
            if length <= 1e-9:
                continue
            edge = RoadEdge(
                edge_id=len(edges),
                start_node=start_node,
                end_node=end_node,
                source_feature_index=segment.feature.source_feature_index,
                source_feature_id=segment.feature.source_feature_id,
                centerline=(nodes[start_node].coordinate, nodes[end_node].coordinate),
                length_meters=length,
                properties=dict(segment.feature.properties),
            )
            edges.append(edge)
            nodes[start_node].edge_ids.append(edge.edge_id)
            nodes[end_node].edge_ids.append(edge.edge_id)

    return RoadNetwork(
        nodes=nodes,
        edges=edges,
        source_road_count=len(road_features),
        noded_intersection_count=len(intersection_coordinates),
    )


def extract_road_network(features, projection, source_feature_indices=None, node_tolerance_meters=0.05):
    roads = extract_road_features(features, projection, source_feature_indices)
    return build_road_network(roads, projection, node_tolerance_meters)