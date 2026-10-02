"""Validity checks for GeoJSON geometry before procedural generation."""

import math
from dataclasses import dataclass

from shapely.geometry import shape
from shapely.validation import explain_validity


@dataclass(frozen=True)
class ValidationResult:
    source_feature_index: int
    valid: bool
    generation_ready: bool
    reasons: tuple
    repaired: bool
    status: str
    issue_details: tuple = ()

    def to_dict(self):
        return {
            "source_feature_index": self.source_feature_index,
            "valid": self.valid,
            "generation_ready": self.generation_ready,
            "reasons": list(self.reasons),
            "repaired": self.repaired,
            "status": self.status,
            "issue_details": list(self.issue_details),
        }


@dataclass(frozen=True)
class GridRectificationResult:
    grid_id: int
    rectification: str
    affected_features: tuple
    reason: str | None
    failure_feature_indices: tuple = ()
    reasons: tuple = ()

    def to_dict(self):
        return {
            "grid_id": self.grid_id,
            "rectification": self.rectification,
            "affected_features": list(self.affected_features),
            "reason": self.reason,
            "failure_feature_indices": list(self.failure_feature_indices),
            "reasons": list(self.reasons),
        }


def ordered_issues(issues):
    unique = {}
    for issue in issues:
        key = (
            issue.get("code", "unknown"),
            issue.get("message", ""),
            issue.get("other_feature_index", -1),
        )
        unique[key] = issue
    return [unique[key] for key in sorted(unique)]


def _is_position(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    )


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
    if geometry is None:
        return None
    projected = dict(geometry)
    if "coordinates" in projected:
        projected["coordinates"] = _project_value(projected["coordinates"], projection)
    if "geometries" in projected:
        projected["geometries"] = [
            _project_geometry(child, projection) for child in projected["geometries"]
        ]
    return projected


def _sequences(geometry):
    if not geometry:
        return
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "LineString":
        yield coordinates, False
    elif geometry_type == "MultiLineString":
        for line in coordinates:
            yield line, False
    elif geometry_type == "Polygon":
        for ring in coordinates:
            yield ring, True
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            for ring in polygon:
                yield ring, True
    elif geometry_type == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from _sequences(child)


def _polygon_metrics(geometry):
    if geometry is None or geometry.is_empty:
        return 0.0, 0
    if geometry.geom_type == "Polygon":
        return geometry.area, len(geometry.interiors)
    if geometry.geom_type == "MultiPolygon":
        return (
            sum(polygon.area for polygon in geometry.geoms),
            sum(len(polygon.interiors) for polygon in geometry.geoms),
        )
    if geometry.geom_type == "GeometryCollection":
        metrics = [_polygon_metrics(child) for child in geometry.geoms]
        return sum(area for area, _ in metrics), sum(holes for _, holes in metrics)
    return 0.0, 0


def _geometry_shape(geometry, projection):
    if geometry is None:
        return None
    return shape(_project_geometry(geometry, projection))


def validate_geometry(source_geometry, candidate_geometry, projection):
    """Validate rectified geometry and compare area/hole topology to its source."""
    issues = []
    if candidate_geometry is None:
        issues.append({"code": "missing_geometry", "message": "Feature has no geometry"})
    source_shape = None
    candidate_shape = None
    try:
        source_shape = _geometry_shape(source_geometry, projection)
    except Exception as error:
        issues.append({"code": "invalid_source_geometry", "message": str(error)})
    try:
        candidate_shape = _geometry_shape(candidate_geometry, projection)
    except Exception as error:
        issues.append({"code": "invalid_geometry", "message": str(error)})

    if candidate_geometry is not None:
        for sequence, is_ring in _sequences(candidate_geometry):
            if not isinstance(sequence, (list, tuple)) or not all(
                _is_position(position) for position in sequence
            ):
                issues.append({"code": "invalid_coordinate", "message": "Geometry sequence contains an invalid position"})
                continue
            if is_ring and (
                len(sequence) < 4
                or sequence[0][:2] != sequence[-1][:2]
                or len({tuple(position[:2]) for position in sequence[:-1]}) < 3
            ):
                issues.append({"code": "invalid_ring", "message": "Polygon ring is open or degenerate"})
            for start, end in zip(sequence, sequence[1:]):
                start_x, start_y = projection.forward(start[0], start[1])
                end_x, end_y = projection.forward(end[0], end[1])
                if math.hypot(end_x - start_x, end_y - start_y) <= 1e-9:
                    issues.append({"code": "zero_length_edge", "message": "Geometry contains a zero-length edge"})

    source_area, source_holes = _polygon_metrics(source_shape)
    candidate_area, candidate_holes = _polygon_metrics(candidate_shape)
    if candidate_shape is not None and not candidate_shape.is_valid:
        reason = explain_validity(candidate_shape)
        lowered_reason = reason.lower()
        code = "self_intersection" if "self-intersection" in lowered_reason else "invalid_geometry"
        issues.append({"code": code, "message": reason})
    if source_area > 0 and candidate_geometry is not None:
        if candidate_area <= max(1e-8, source_area * 1e-8):
            issues.append({"code": "collapsed_polygon", "message": "Polygon area collapsed during rectification"})
    if candidate_geometry is not None and candidate_holes != source_holes:
        issues.append({
            "code": "unexpected_holes",
            "message": f"Hole count changed from {source_holes} to {candidate_holes}",
        })

    unique_issues = []
    seen = set()
    for issue in issues:
        key = (issue["code"], issue["message"])
        if key not in seen:
            seen.add(key)
            unique_issues.append(issue)
    return {
        "valid": not unique_issues,
        "issues": unique_issues,
        "area_before_m2": source_area,
        "area_after_m2": candidate_area,
        "holes_before": source_holes,
        "holes_after": candidate_holes,
    }


def geometry_validation_gate(source_geometry, candidate_geometry, projection):
    """Accept a valid candidate, restore valid source geometry, or reject source."""
    candidate_report = validate_geometry(source_geometry, candidate_geometry, projection)
    if candidate_report["valid"]:
        return candidate_geometry, candidate_report, "passed", []

    source_report = validate_geometry(source_geometry, source_geometry, projection)
    if source_report["valid"]:
        return source_geometry, source_report, "restored_source", candidate_report["issues"]
    return source_geometry, source_report, "invalid_source", candidate_report["issues"]


def validate_topology(source_geometries, candidate_geometries, grid_ids, projection, tolerance_meters):
    """Check that existing inter-feature intersections survive rectification."""
    from shapely.strtree import STRtree

    source_shapes = []
    candidate_shapes = []
    feature_indices = []
    for index, (source, candidate, grid_id) in enumerate(
        zip(source_geometries, candidate_geometries, grid_ids)
    ):
        if grid_id is None or source is None or candidate is None:
            continue
        try:
            source_shapes.append(_geometry_shape(source, projection))
            candidate_shapes.append(_geometry_shape(candidate, projection))
            feature_indices.append(index)
        except Exception:
            continue

    if not source_shapes:
        return {}

    tree = STRtree(source_shapes)
    issues = {}
    for local_index, source_shape in enumerate(source_shapes):
        feature_index = feature_indices[local_index]
        for matched in tree.query(source_shape):
            other_index = int(matched)
            if other_index <= local_index:
                continue
            other_feature_index = feature_indices[other_index]
            if grid_ids[feature_index] != grid_ids[other_feature_index]:
                continue
            source_intersection = source_shape.intersection(source_shapes[other_index])
            if source_intersection.is_empty:
                continue
            try:
                candidate_intersection = candidate_shapes[local_index].intersection(
                    candidate_shapes[other_index]
                )
            except Exception:
                issue = {
                    "code": "topology_inconsistency",
                    "message": "Candidate intersection failed because rectified topology is invalid",
                    "other_feature_index": other_feature_index,
                }
                issues.setdefault(feature_index, []).append(issue)
                issues.setdefault(other_feature_index, []).append({
                    **issue,
                    "other_feature_index": feature_index,
                })
                continue
            lost_shared_edge = (
                source_intersection.length > tolerance_meters
                and candidate_intersection.length <= tolerance_meters
            )
            lost_connection = (
                source_intersection.length <= tolerance_meters
                and candidate_intersection.is_empty
            )
            if lost_shared_edge or lost_connection:
                issue = {
                    "code": "topology_inconsistency",
                    "message": f"Intersection with feature {other_feature_index} was lost",
                    "other_feature_index": other_feature_index,
                }
                issues.setdefault(feature_index, []).append(issue)
                issues.setdefault(other_feature_index, []).append({
                    **issue,
                    "other_feature_index": feature_index,
                })
    return issues