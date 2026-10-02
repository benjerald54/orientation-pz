import unittest

from orientation_detector import ProjectionContext, annotate_collection
from road_network import extract_road_features, extract_road_network


class RoadNetworkTests(unittest.TestCase):
    @staticmethod
    def _road(coordinates, **properties):
        return {
            "type": "Feature",
            "properties": {"highway": "residential", **properties},
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }

    def test_t_intersection_is_a_degree_three_node(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]]),
            self._road([[0, 0], [0, 0.001]]),
        ]
        network = extract_road_network(features, ProjectionContext(0))
        self.assertEqual(len(network.nodes), 4)
        self.assertEqual(len(network.edges), 3)
        self.assertEqual(sorted(node.degree for node in network.nodes), [1, 1, 1, 3])
        self.assertEqual(network.noded_intersection_count, 1)

    def test_crossing_roads_share_a_degree_four_node(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]]),
            self._road([[0, -0.001], [0, 0.001]]),
        ]
        network = extract_road_network(features, ProjectionContext(0))
        self.assertEqual(len(network.nodes), 5)
        self.assertEqual(len(network.edges), 4)
        self.assertEqual(sorted(node.degree for node in network.nodes), [1, 1, 1, 1, 4])
        self.assertEqual(network.noded_intersection_count, 1)
        exact_network = extract_road_network(features, ProjectionContext(0), node_tolerance_meters=0)
        self.assertEqual(len(exact_network.connected_components()), 1)
        self.assertEqual(sorted(node.degree for node in exact_network.nodes), [1, 1, 1, 1, 4])

    def test_parallel_roads_remain_disconnected(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]]),
            self._road([[-0.001, 0.00005], [0.001, 0.00005]]),
        ]
        network = extract_road_network(features, ProjectionContext(0))
        self.assertEqual(len(network.nodes), 4)
        self.assertEqual(len(network.edges), 2)
        self.assertEqual(len(network.connected_components()), 2)
        self.assertEqual(network.noded_intersection_count, 0)

    def test_bridge_crossing_is_not_connected_to_at_grade_road(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]], layer="0"),
            self._road([[0, -0.001], [0, 0.001]], layer="1", bridge="yes"),
        ]
        network = extract_road_network(features, ProjectionContext(0))
        self.assertEqual(len(network.nodes), 4)
        self.assertEqual(len(network.edges), 2)
        self.assertEqual(len(network.connected_components()), 2)
        self.assertEqual(network.noded_intersection_count, 0)

    def test_normalized_centerline_removes_consecutive_duplicate_vertices(self):
        features = [self._road([[0, 0], [0.0005, 0], [0.0005, 0], [0.001, 0]])]
        roads = extract_road_features(features, ProjectionContext(0))
        self.assertEqual(len(roads), 1)
        self.assertEqual(len(roads[0].centerlines[0]), 3)

    def test_compiler_emits_validated_road_network(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]]),
            self._road([[0, 0], [0, 0.001]]),
        ]
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        network = result["road_network"]
        self.assertEqual(network["summary"]["node_count"], 4)
        self.assertEqual(network["summary"]["edge_count"], 3)
        self.assertEqual(network["summary"]["connected_component_count"], 1)
        self.assertEqual(
            {edge["source_feature_index"] for edge in network["edges"]},
            {0, 1},
        )


if __name__ == "__main__":
    unittest.main()
