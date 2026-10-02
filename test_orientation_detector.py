import math
import unittest
from unittest.mock import patch

from orientation_detector import (
    ProjectionContext,
    _build_topology_graph,
    annotate_collection,
    detect_orientation,
)
from geometry_validation import geometry_validation_gate, validate_geometry, validate_topology


class OrientationDetectorTests(unittest.TestCase):
    @staticmethod
    def _line_at_bearing(degrees, start=(0, 0), length=0.001):
        angle = math.radians(degrees)
        return {
            "type": "LineString",
            "coordinates": [
                list(start),
                [start[0] + length * math.cos(angle), start[1] + length * math.sin(angle)],
            ],
        }

    @staticmethod
    def _building_at_bearing(degrees, start):
        angle = math.radians(degrees)
        along = (0.00001 * math.cos(angle), 0.00001 * math.sin(angle))
        across = (-0.000004 * math.sin(angle), 0.000004 * math.cos(angle))
        origin = list(start)
        first = [origin[0] + along[0], origin[1] + along[1]]
        second = [first[0] + across[0], first[1] + across[1]]
        third = [origin[0] + across[0], origin[1] + across[1]]
        return {"type": "Polygon", "coordinates": [[origin, first, second, third, origin]]}

    @staticmethod
    def _building_near_grid_eligibility_boundary(cut, origin=(0, 0), width=0.0001):
        notch_half_width = width / 10
        return {
            "type": "Polygon",
            "coordinates": [[
                list(origin),
                [origin[0] + width, origin[1]],
                [origin[0] + width, origin[1] + width],
                [origin[0] + width - notch_half_width, origin[1] + width],
                [origin[0] + width / 2, origin[1] + width - cut],
                [origin[0] + notch_half_width, origin[1] + width],
                [origin[0], origin[1] + width],
                list(origin),
            ]],
        }

    def test_projection_context_round_trips_world_coordinates(self):
        projection = ProjectionContext(52.5)
        x, y = projection.forward(-122.3, 52.501)
        longitude, latitude = projection.inverse(x, y)
        self.assertAlmostEqual(longitude, -122.3, places=12)
        self.assertAlmostEqual(latitude, 52.501, places=12)

    def test_topology_node_identity_uses_metric_tolerance(self):
        first_line = {"type": "LineString", "coordinates": [[0, 0], [0.001, 0]]}

        def graph_for_latitude_offset(offset):
            geometries = {
                0: first_line,
                1: {"type": "LineString", "coordinates": [[0, offset], [0.001, offset]]},
            }
            policies = {0: ("generic", 15), 1: ("generic", 15)}
            return _build_topology_graph(
                geometries, policies, ProjectionContext(0), tolerance_meters=0.05
            )

        within_tolerance = graph_for_latitude_offset(0.0000003)
        outside_tolerance = graph_for_latitude_offset(0.000001)
        self.assertEqual(len(within_tolerance.nodes), 2)
        self.assertEqual(len(outside_tolerance.nodes), 4)
        self.assertEqual(
            within_tolerance.node_by_coordinate[(0, 0)],
            within_tolerance.node_by_coordinate[(0, 0.0000003)],
        )
        self.assertNotEqual(
            outside_tolerance.node_by_coordinate[(0, 0)],
            outside_tolerance.node_by_coordinate[(0, 0.000001)],
        )

    def test_geometry_validation_detects_self_intersection_and_collapse(self):
        projection = ProjectionContext(0)
        square = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]]],
        }
        bow_tie = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0.001, 0.001], [0, 0.001], [0.001, 0], [0, 0]]],
        }
        collapsed = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0, 0], [0, 0], [0, 0]]],
        }

        crossing_report = validate_geometry(square, bow_tie, projection)
        collapsed_report = validate_geometry(square, collapsed, projection)
        self.assertIn("self_intersection", {issue["code"] for issue in crossing_report["issues"]})
        self.assertIn("collapsed_polygon", {issue["code"] for issue in collapsed_report["issues"]})

        for candidate, expected_issue in ((bow_tie, "self_intersection"), (collapsed, "collapsed_polygon")):
            restored, report, status, candidate_issues = geometry_validation_gate(
                square, candidate, projection
            )
            self.assertEqual(restored, square)
            self.assertTrue(report["valid"])
            self.assertEqual(status, "restored_source")
            self.assertIn(expected_issue, {issue["code"] for issue in candidate_issues})

    def test_geometry_validation_detects_zero_edges_and_open_rings(self):
        polygon = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0.001, 0], [0.001, 0], [0, 0.001]]],
        }
        report = validate_geometry(polygon, polygon, ProjectionContext(0))
        issue_codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("zero_length_edge", issue_codes)
        self.assertIn("invalid_ring", issue_codes)

    def test_geometry_validation_reports_lost_courtyard(self):
        source = {
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]],
                [[0.0002, 0.0002], [0.0008, 0.0002], [0.0008, 0.0008],
                 [0.0002, 0.0008], [0.0002, 0.0002]],
            ],
        }
        filled = {"type": "Polygon", "coordinates": [source["coordinates"][0]]}
        report = validate_geometry(source, filled, ProjectionContext(0))
        self.assertIn("unexpected_holes", {issue["code"] for issue in report["issues"]})

    def test_topology_validation_reports_lost_shared_wall(self):
        first = {"type": "Polygon", "coordinates": [[
            [0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]
        ]]}
        second = {"type": "Polygon", "coordinates": [[
            [0.001, 0], [0.002, 0], [0.002, 0.001], [0.001, 0.001], [0.001, 0]
        ]]}
        separated = {"type": "Polygon", "coordinates": [[
            [0.001001, 0], [0.002001, 0], [0.002001, 0.001],
            [0.001001, 0.001], [0.001001, 0]
        ]]}
        issues = validate_topology(
            [first, second], [first, separated], [1, 1], ProjectionContext(0), 0.05
        )
        self.assertIn("topology_inconsistency", {issue["code"] for issue in issues[0]})

    def test_invalid_source_is_excluded_from_generation(self):
        bow_tie = {
            "type": "Feature",
            "properties": {"building": "yes"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [0.001, 0.001], [0, 0.001], [0.001, 0], [0, 0]]],
            },
        }
        result = annotate_collection({"type": "FeatureCollection", "features": [bow_tie]})
        feature = result["features"][0]
        self.assertFalse(feature["properties"]["generation_eligible"])
        self.assertFalse(feature["properties"]["geometry_validation"]["valid"])
        self.assertEqual(result["generation_ready_feature_indices"], [])
        report = result["validation_report"]["features"][0]
        self.assertEqual(report["source_feature_index"], 0)
        self.assertFalse(report["valid"])
        self.assertFalse(report["generation_ready"])
        self.assertIn("self_intersection", report["reasons"])
        self.assertFalse(report["repaired"])

    def test_grid_rollback_report_is_deterministic_and_lists_affected_features(self):
        features = [
            {"type": "Feature", "properties": {},
             "geometry": self._line_at_bearing(0, (index * 0.00001, 0))}
            for index in range(3)
        ]
        invalid_candidate = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0.001, 0.001], [0, 0.001], [0.001, 0], [0, 0]]],
        }
        forced_rectifications = {
            0: (invalid_candidate, 1, 0),
            1: (features[1]["geometry"], 0, 0),
            2: (features[2]["geometry"], 0, 0),
        }

        with patch("orientation_detector._rectify_grid_features", return_value=forced_rectifications):
            first = annotate_collection({"type": "FeatureCollection", "features": features})
        with patch("orientation_detector._rectify_grid_features", return_value=forced_rectifications):
            second = annotate_collection({"type": "FeatureCollection", "features": features})

        grid_report = first["validation_report"]["grid_rectifications"][0]
        self.assertEqual(first["validation_report"], second["validation_report"])
        feature_reports = first["validation_report"]["features"]
        self.assertEqual([item["source_feature_index"] for item in feature_reports], [0, 1, 2])
        self.assertTrue(all(item["reasons"] == sorted(item["reasons"]) for item in feature_reports))
        self.assertEqual(grid_report["rectification"], "rolled_back")
        self.assertEqual(grid_report["affected_features"], [0, 1, 2])
        self.assertEqual(grid_report["reason"], "validation_failure")
        self.assertIn(0, grid_report["failure_feature_indices"])
        self.assertEqual(grid_report["reasons"], sorted(grid_report["reasons"]))
        self.assertIn("self_intersection", grid_report["reasons"])
        self.assertEqual(first["features"][0]["geometry"], features[0]["geometry"])
        self.assertTrue(all(feature["properties"]["generation_eligible"] for feature in first["features"]))

    def test_detects_east_west_road(self):
        geometry = {"type": "LineString", "coordinates": [[0, 0], [10, 0.2]]}
        self.assertEqual(detect_orientation(geometry)[0], "horizontal")

    def test_detects_north_south_road(self):
        geometry = {"type": "LineString", "coordinates": [[0, 0], [0.1, 10]]}
        self.assertEqual(detect_orientation(geometry)[0], "vertical")

    def test_square_footprint_is_undetermined(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]],
        }
        self.assertEqual(detect_orientation(geometry)[0], "undetermined")

    def test_spiked_footprint_keeps_main_orientation(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [10, 0], [10, 3], [6, 3], [5, 30], [4, 3], [0, 3], [0, 0]]],
        }
        self.assertEqual(detect_orientation(geometry)[0], "horizontal")

    def test_annotates_features_and_summary(self):
        collection = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"kind": "road"}, "geometry": {
                    "type": "LineString", "coordinates": [[0, 0], [10, 0]]
                }},
                {"type": "Feature", "properties": {}, "geometry": {
                    "type": "LineString", "coordinates": [[0, 0], [0, 10]]
                }},
            ],
        }
        result = annotate_collection(collection)
        self.assertEqual(result["features"][0]["properties"]["dominant_orientation_degrees"], 0)
        self.assertEqual(result["orientation_summary"]["dominant_orientation"], "undetermined")

    def test_discovers_local_grids_by_space_and_angle(self):
        features = []
        groups = (
            ((10, 11, 12), (0, 0)),
            ((35, 36, 37), (0, 0)),
            ((35, 36, 37), (0.02, 0)),
        )
        for bearings, start in groups:
            for bearing in bearings:
                features.append({
                    "type": "Feature",
                    "properties": {},
                    "geometry": self._line_at_bearing(bearing, start),
                })
        diagonal = {
            "type": "Feature",
            "properties": {},
            "geometry": self._line_at_bearing(70),
        }
        features.append(diagonal)

        result = annotate_collection({"type": "FeatureCollection", "features": features})
        aligned, co_located_rotated, distant_same_bearing, diagonal_result = result["features"][:3], result["features"][3:6], result["features"][6:9], result["features"][9]
        aligned_id = aligned[0]["properties"]["local_grid_id"]
        rotated_id = co_located_rotated[0]["properties"]["local_grid_id"]
        distant_id = distant_same_bearing[0]["properties"]["local_grid_id"]
        self.assertEqual({feature["properties"]["local_grid_id"] for feature in aligned}, {aligned_id})
        self.assertEqual({feature["properties"]["local_grid_id"] for feature in co_located_rotated}, {rotated_id})
        self.assertEqual({feature["properties"]["local_grid_id"] for feature in distant_same_bearing}, {distant_id})
        self.assertEqual(len({aligned_id, rotated_id, distant_id}), 3)
        self.assertAlmostEqual(aligned[0]["properties"]["grid_bearing_degrees"], 11, places=1)
        self.assertAlmostEqual(co_located_rotated[0]["properties"]["grid_bearing_degrees"], 36, places=1)
        self.assertAlmostEqual(distant_same_bearing[0]["properties"]["grid_bearing_degrees"], 36, places=1)
        for feature in aligned + co_located_rotated + distant_same_bearing:
            bearing = feature["properties"]["grid_bearing_degrees"]
            start, end = feature["geometry"]["coordinates"]
            angle = math.radians(bearing)
            delta_x, delta_y = end[0] - start[0], end[1] - start[1]
            cross_axis_delta = -math.sin(angle) * delta_x + math.cos(angle) * delta_y
            self.assertAlmostEqual(cross_axis_delta, 0, places=10)
        self.assertIsNone(diagonal_result["properties"]["local_grid_id"])
        self.assertEqual(diagonal_result["geometry"]["coordinates"], diagonal["geometry"]["coordinates"])
        self.assertFalse(diagonal_result["properties"]["geometry_orthogonalized"])
        self.assertEqual(result["orthogonalization_summary"]["domains_detected"], 3)

    def test_separates_physically_overlapping_grids_by_bearing(self):
        features = []
        for bearings in ((9, 10, 11), (39, 40, 41)):
            for bearing in bearings:
                features.append({
                    "type": "Feature",
                    "properties": {},
                    "geometry": self._line_at_bearing(bearing),
                })

        result = annotate_collection({"type": "FeatureCollection", "features": features})
        first_grid, second_grid = result["features"][:3], result["features"][3:]
        first_ids = {feature["properties"]["local_grid_id"] for feature in first_grid}
        second_ids = {feature["properties"]["local_grid_id"] for feature in second_grid}
        self.assertEqual(len(first_ids), 1)
        self.assertEqual(len(second_ids), 1)
        self.assertNotEqual(first_ids, second_ids)
        self.assertAlmostEqual(first_grid[1]["properties"]["grid_bearing_degrees"], 10, places=1)
        self.assertAlmostEqual(second_grid[1]["properties"]["grid_bearing_degrees"], 40, places=1)
        self.assertEqual(result["orthogonalization_summary"]["domains_detected"], 2)

    def test_does_not_assign_feature_without_a_bearing(self):
        features = [
            {"type": "Feature", "properties": {}, "geometry": self._line_at_bearing(10, (index * 0.00001, 0))}
            for index in range(3)
        ]
        ambiguous = {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "GeometryCollection",
                "geometries": [
                    self._line_at_bearing(0),
                    self._line_at_bearing(45),
                ],
            },
        }
        features.append(ambiguous)

        result = annotate_collection({"type": "FeatureCollection", "features": features})
        ambiguous_result = result["features"][-1]
        self.assertIsNone(ambiguous_result["properties"]["grid_bearing_degrees"])
        self.assertIsNone(ambiguous_result["properties"]["local_grid_id"])
        self.assertEqual(ambiguous_result["geometry"], ambiguous["geometry"])

    def test_domain_and_rectification_tolerances_are_independent(self):
        features = [
            {"type": "Feature", "properties": {}, "geometry": self._line_at_bearing(
                degrees, (index * 0.0001, 0), length=0.00001
            )}
            for index, degrees in enumerate((0, 6, 12))
        ]
        collection = {"type": "FeatureCollection", "features": features}
        result = annotate_collection(
            collection,
            domain_angle_tolerance_degrees=7,
            rectification_angle_tolerance_degrees=2,
        )

        grid_ids = {feature["properties"]["local_grid_id"] for feature in result["features"]}
        self.assertEqual(len(grid_ids), 1)
        self.assertEqual(result["orthogonalization_summary"]["domain_angle_tolerance_degrees"], 7)
        self.assertEqual(result["orthogonalization_summary"]["rectification_angle_tolerance_degrees"], 2)
        self.assertEqual(result["features"][2]["geometry"], features[2]["geometry"])
        self.assertFalse(result["features"][2]["properties"]["geometry_orthogonalized"])

    def test_rectification_policy_respects_feature_semantics(self):
        features = [
            {
                "type": "Feature",
                "properties": {"highway": "residential"},
                "geometry": self._line_at_bearing(degrees, (index * 0.0001, 0), length=0.00001),
            }
            for index, degrees in enumerate((0, 0, 0, 12))
        ]
        building = {
            "type": "Feature",
            "properties": {"building": "yes"},
            "geometry": self._building_at_bearing(4, (0.0004, 0)),
        }
        oblique_building = {
            "type": "Feature",
            "properties": {"building": "yes"},
            "geometry": self._building_at_bearing(12, (0.0005, 0)),
        }
        water = {
            "type": "Feature",
            "properties": {"natural": "water"},
            "geometry": self._line_at_bearing(1, (0.0006, 0), length=0.00001),
        }
        features.extend((building, oblique_building, water))

        result = annotate_collection({"type": "FeatureCollection", "features": features})
        road_result = result["features"][3]
        building_result = result["features"][4]
        oblique_building_result = result["features"][5]
        water_result = result["features"][6]
        self.assertTrue(road_result["properties"]["geometry_orthogonalized"])
        self.assertEqual(road_result["properties"]["rectification_semantic_class"], "transportation")
        self.assertTrue(building_result["properties"]["geometry_orthogonalized"])
        self.assertEqual(building_result["properties"]["rectification_angle_tolerance_degrees"], 5)
        self.assertFalse(oblique_building_result["properties"]["geometry_orthogonalized"])
        self.assertIsNone(oblique_building_result["properties"]["rectification_angle_tolerance_degrees"])
        self.assertFalse(water_result["properties"]["geometry_orthogonalized"])
        self.assertIsNone(water_result["properties"]["rectification_angle_tolerance_degrees"])

    def test_unassigned_building_is_left_unchanged(self):
        building = {
            "type": "Feature",
            "properties": {"building": "yes"},
            "geometry": self._building_at_bearing(2, (0, 0)),
        }
        result = annotate_collection({"type": "FeatureCollection", "features": [building]})
        feature = result["features"][0]
        self.assertEqual(feature["geometry"], building["geometry"])
        self.assertIsNone(feature["properties"]["local_grid_id"])
        self.assertIsNone(feature["properties"]["rectification_angle_tolerance_degrees"])
        self.assertTrue(feature["properties"]["geometry_validation"]["valid"])
        self.assertTrue(feature["properties"]["generation_eligible"])

    def test_two_buildings_keep_shared_wall_and_pass_validation(self):
        support_roads = [
            {"type": "Feature", "properties": {"highway": "residential"},
             "geometry": self._line_at_bearing(0, start, length=0.00002)}
            for start in ((-0.0001, -0.0001), (0.00005, -0.0001), (0.0002, -0.0001))
        ]
        first_ring = [[0, 0], [0.0001, 0], [0.0001, 0.0001], [0, 0.0001], [0, 0]]
        second_ring = [
            [0.0001, 0], [0.0002, 0], [0.0002, 0.0001], [0.0001, 0.0001], [0.0001, 0]
        ]
        buildings = [
            {"type": "Feature", "properties": {"building": "yes"},
             "geometry": {"type": "Polygon", "coordinates": [ring]}}
            for ring in (first_ring, second_ring)
        ]
        result = annotate_collection({"type": "FeatureCollection", "features": support_roads + buildings})
        first, second = result["features"][-2:]
        first_wall = {tuple(position[:2]) for position in first["geometry"]["coordinates"][0]
                      if abs(position[0] - 0.0001) < 1e-12}
        second_wall = {tuple(position[:2]) for position in second["geometry"]["coordinates"][0]
                       if abs(position[0] - 0.0001) < 1e-12}
        self.assertEqual(first_wall, second_wall)
        self.assertTrue(first["properties"]["geometry_validation"]["valid"])
        self.assertTrue(second["properties"]["geometry_validation"]["valid"])
        self.assertTrue(first["properties"]["generation_eligible"])
        self.assertTrue(second["properties"]["generation_eligible"])

    def test_t_intersection_remains_connected_after_validation(self):
        features = [
            {"type": "Feature", "properties": {"highway": "residential"}, "geometry": {
                "type": "LineString", "coordinates": [[-0.001, 0], [0.001, 0]]
            }},
            {"type": "Feature", "properties": {"highway": "residential"}, "geometry": {
                "type": "LineString", "coordinates": [[0, 0], [0, 0.0008]]
            }},
            {"type": "Feature", "properties": {"highway": "residential"}, "geometry": {
                "type": "LineString", "coordinates": [[-0.001, -0.0003], [0.001, -0.0003]]
            }},
        ]
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        main_road, branch = [feature["geometry"]["coordinates"] for feature in result["features"][:2]]
        junction = main_road[1]
        self.assertAlmostEqual(junction[0], 0, places=12)
        self.assertAlmostEqual(junction[1], 0, places=12)
        self.assertEqual(branch[0][:2], junction[:2])
        self.assertTrue(all(feature["properties"]["generation_eligible"] for feature in result["features"]))

    def test_tiny_building_keeps_positive_area(self):
        supports = [
            {"type": "Feature", "properties": {"highway": "residential"},
             "geometry": self._line_at_bearing(0, start, length=0.00002)}
            for start in ((-0.00001, -0.00002), (0, -0.00002), (0.00001, -0.00002))
        ]
        tiny = {
            "type": "Feature",
            "properties": {"building": "yes"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [0, 0], [0.000001, 0], [0.000001, 0.000001], [0, 0.000001], [0, 0]
            ]]},
        }
        result = annotate_collection({"type": "FeatureCollection", "features": supports + [tiny]})
        building = result["features"][-1]
        validation = building["properties"]["geometry_validation"]
        self.assertGreater(validation["area_after_m2"], 0)
        self.assertTrue(validation["valid"])
        self.assertTrue(building["properties"]["generation_eligible"])

    def test_building_courtyard_is_preserved(self):
        supports = [
            {"type": "Feature", "properties": {"highway": "residential"},
             "geometry": self._line_at_bearing(0, start, length=0.00002)}
            for start in ((-0.00001, -0.00002), (0, -0.00002), (0.00001, -0.00002))
        ]
        courtyard_building = {
            "type": "Feature",
            "properties": {"building": "yes"},
            "geometry": {"type": "Polygon", "coordinates": [
                [[0, 0], [0.0001, 0], [0.0001, 0.0001], [0, 0.0001], [0, 0]],
                [[0.00003, 0.00003], [0.00007, 0.00003], [0.00007, 0.00007],
                 [0.00003, 0.00007], [0.00003, 0.00003]],
            ]},
        }
        result = annotate_collection({"type": "FeatureCollection", "features": supports + [courtyard_building]})
        building = result["features"][-1]
        self.assertEqual(len(building["geometry"]["coordinates"][1:]), 1)
        self.assertEqual(building["properties"]["geometry_validation"]["holes_after"], 1)
        self.assertTrue(building["properties"]["generation_eligible"])

    def test_building_eligibility_is_stable_near_75_percent_boundary(self):
        width = 0.0001
        notch_half_width = width / 10
        aligned_perimeter = 3 * width + 2 * notch_half_width
        diagonal_half_length = aligned_perimeter / 6
        boundary_cut = math.sqrt(
            diagonal_half_length ** 2 - (width / 2 - notch_half_width) ** 2
        )

        def result_for(cut):
            supports = [
                {"type": "Feature", "properties": {"highway": "residential"},
                 "geometry": self._line_at_bearing(0, start, length=0.00002)}
                for start in ((-0.00001, -0.00002), (0, -0.00002), (0.00001, -0.00002))
            ]
            building = {
                "type": "Feature",
                "properties": {"building": "yes"},
                "geometry": self._building_near_grid_eligibility_boundary(cut, width=width),
            }
            result = annotate_collection({"type": "FeatureCollection", "features": supports + [building]})
            return result["features"][-1]["properties"]["rectification_angle_tolerance_degrees"]

        boundary_result = result_for(boundary_cut)
        self.assertEqual(boundary_result, result_for(boundary_cut))
        self.assertEqual(result_for(boundary_cut * 0.999), 5)
        self.assertIsNone(result_for(boundary_cut * 1.001))

    def test_nodes_proper_segment_intersections(self):
        features = [
            {"type": "Feature", "properties": {}, "geometry": {
                "type": "LineString", "coordinates": [[-0.001, 0], [0.001, 0]]
            }},
            {"type": "Feature", "properties": {}, "geometry": {
                "type": "LineString", "coordinates": [[0, -0.001], [0, 0.001]]
            }},
            {"type": "Feature", "properties": {}, "geometry": {
                "type": "LineString", "coordinates": [[-0.001, 0.0008], [0.001, 0.0008]]
            }},
        ]
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        horizontal, vertical, upper_horizontal = [
            feature["geometry"]["coordinates"] for feature in result["features"]
        ]
        for coordinate in (horizontal[1], vertical[1]):
            self.assertAlmostEqual(coordinate[0], 0, places=12)
            self.assertAlmostEqual(coordinate[1], 0, places=12)
        for coordinate in (vertical[2], upper_horizontal[1]):
            self.assertAlmostEqual(coordinate[0], 0, places=12)
            self.assertAlmostEqual(coordinate[1], 0.0008, places=12)
        self.assertEqual(result["orthogonalization_summary"]["topology_nodes_inserted"], 4)

    def test_orthogonalizes_polygon_ring_and_preserves_closure(self):
        angle = math.radians(10)
        cosine, sine = math.cos(angle), math.sin(angle)
        ring = [
            [0, 0],
            [0.001 * cosine, 0.001 * sine],
            [0.001 * cosine - 0.0004 * sine + 0.00005, 0.001 * sine + 0.0004 * cosine],
            [-0.0004 * sine, 0.0004 * cosine],
            [0, 0],
        ]
        shared_first = ring[1]
        shared_second = ring[2]
        shared_midpoint = [
            (shared_first[0] + shared_second[0]) / 2,
            (shared_first[1] + shared_second[1]) / 2,
        ]
        along_x, along_y = 0.001 * cosine, 0.001 * sine
        adjacent_ring = [
            shared_second,
            shared_midpoint,
            shared_first,
            [shared_first[0] + along_x, shared_first[1] + along_y],
            [shared_second[0] + along_x, shared_second[1] + along_y],
            shared_second,
        ]
        features = [
            {"type": "Feature", "properties": {"neighborhood_id": "grid"}, "geometry": self._line_at_bearing(10)},
            {"type": "Feature", "properties": {"neighborhood_id": "grid"}, "geometry": self._line_at_bearing(10)},
            {"type": "Feature", "properties": {"neighborhood_id": "grid"}, "geometry": {
                "type": "Polygon", "coordinates": [ring]
            }},
            {"type": "Feature", "properties": {"neighborhood_id": "grid"}, "geometry": {
                "type": "Polygon", "coordinates": [adjacent_ring]
            }},
        ]
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        polygon, adjacent = result["features"][2:]
        snapped_ring = polygon["geometry"]["coordinates"][0]
        self.assertTrue(polygon["properties"]["geometry_orthogonalized"])
        self.assertEqual(snapped_ring[0][:2], snapped_ring[-1][:2])
        adjacent_snapped_ring = adjacent["geometry"]["coordinates"][0]
        self.assertEqual(snapped_ring[1][:2], adjacent_snapped_ring[2][:2])
        self.assertEqual(snapped_ring[2][:2], adjacent_snapped_ring[1][:2])
        self.assertEqual(snapped_ring[3][:2], adjacent_snapped_ring[0][:2])
        self.assertGreater(polygon["properties"]["topology_nodes_inserted"], 0)
        self.assertEqual(polygon["properties"]["local_grid_id"], adjacent["properties"]["local_grid_id"])
        self.assertGreater(polygon["properties"]["grid_confidence"], 0)
        local_grid = result["local_grids"][0]
        self.assertEqual(local_grid["geometry"]["type"], "Polygon")
        domain_ring = local_grid["geometry"]["coordinates"][0]
        self.assertEqual(domain_ring[0], domain_ring[-1])
        self.assertEqual(local_grid["properties"]["evidence"]["inlier_count"], 4)
        self.assertGreater(local_grid["properties"]["confidence"], 0)
        self.assertGreater(result["orthogonalization_summary"]["topology_nodes_inserted"], 0)
        bearing = math.radians(polygon["properties"]["grid_bearing_degrees"])
        longitude_scale = math.cos(math.radians(sum(point[1] for point in snapped_ring[:-1]) / (len(snapped_ring) - 1)))
        for start, end in zip(snapped_ring, snapped_ring[1:]):
            delta_x = (end[0] - start[0]) * longitude_scale
            delta_y = end[1] - start[1]
            along = math.cos(bearing) * delta_x + math.sin(bearing) * delta_y
            across = -math.sin(bearing) * delta_x + math.cos(bearing) * delta_y
            self.assertLess(min(abs(along), abs(across)), 1e-7)


if __name__ == "__main__":
    unittest.main()