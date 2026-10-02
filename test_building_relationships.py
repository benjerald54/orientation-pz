import unittest

from building_relationships import build_building_road_relationships
from orientation_detector import ProjectionContext, annotate_collection
from road_network import extract_road_network


class BuildingRoadRelationshipTests(unittest.TestCase):
    @staticmethod
    def _road(coordinates, road_class="residential"):
        return {
            "type": "Feature",
            "properties": {"highway": road_class},
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }

    @staticmethod
    def _building(min_x, min_y, max_x, max_y, **properties):
        return {
            "type": "Feature",
            "properties": {"building": "yes", **properties},
            "geometry": {"type": "Polygon", "coordinates": [[
                [min_x, min_y], [max_x, min_y], [max_x, max_y],
                [min_x, max_y], [min_x, min_y],
            ]]},
        }

    def test_nearest_road_reports_frontage_side_and_local_grid(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]], "primary"),
            self._building(-0.0002, 0.0001, 0.0002, 0.0002,
                           local_grid_id=7, grid_bearing_degrees=0),
        ]
        projection = ProjectionContext(0)
        network = extract_road_network(features, projection)
        relation = build_building_road_relationships(features, network, projection, [1])[0].to_dict()

        self.assertEqual(relation["nearest_road"]["source_feature_index"], 0)
        self.assertEqual(relation["nearest_road"]["road_class"], "primary")
        self.assertAlmostEqual(relation["distance_to_road_meters"], 11.12, places=1)
        self.assertEqual(relation["approach_direction"]["cardinal_direction"], "south")
        self.assertEqual(relation["approach_direction"]["local_grid_side"], "cross_axis_negative")
        self.assertEqual(relation["local_grid_id"], 7)

    def test_building_between_two_roads_reports_both_unique_roads(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]], "primary"),
            self._road([[-0.001, 0.0003], [0.001, 0.0003]], "secondary"),
            self._building(-0.0002, 0.0001, 0.0002, 0.0002),
        ]
        projection = ProjectionContext(0)
        network = extract_road_network(features, projection)
        relation = build_building_road_relationships(
            features, network, projection, [2], adjacency_distance_meters=20
        )[0]

        self.assertEqual(relation.nearest_road["source_feature_index"], 0)
        self.assertEqual(
            [road["source_feature_index"] for road in relation.adjacent_roads], [0, 1]
        )
        self.assertLessEqual(
            max(road["distance_meters"] for road in relation.adjacent_roads), 20
        )

    def test_edges_split_from_one_road_are_aggregated(self):
        features = [
            self._road([[-0.001, 0], [0, 0], [0.001, 0]], "tertiary"),
            self._building(-0.0001, 0.0001, 0.0001, 0.0002),
        ]
        projection = ProjectionContext(0)
        network = extract_road_network(features, projection)
        relation = build_building_road_relationships(features, network, projection, [1])[0]

        self.assertEqual(len(network.edges), 2)
        self.assertEqual(len(relation.adjacent_roads), 1)
        self.assertEqual(relation.adjacent_roads[0]["source_feature_index"], 0)

    def test_compiler_attaches_relationship_only_for_ready_buildings(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]], "primary"),
            self._building(-0.0002, 0.0001, 0.0002, 0.0002),
        ]
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        relationship = result["features"][1]["properties"]["road_relationship"]

        self.assertEqual(relationship["nearest_road"]["road_class"], "primary")
        self.assertEqual(result["building_road_relationships"][0], relationship)
        self.assertEqual(relationship["source_feature_index"], 1)

    def test_building_without_roads_gets_empty_relationship(self):
        features = [self._building(0, 0, 0.0001, 0.0001)]
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        relationship = result["building_road_relationships"][0]
        self.assertIsNone(relationship["nearest_road"])
        self.assertIsNone(relationship["distance_to_road_meters"])
        self.assertEqual(relationship["adjacent_roads"], [])


if __name__ == "__main__":
    unittest.main()
