import unittest

from orientation_detector import annotate_collection


class BlockDetectionTests(unittest.TestCase):
    @staticmethod
    def _road(coordinates, **properties):
        return {
            "type": "Feature",
            "properties": {"highway": "residential", **properties},
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }

    @staticmethod
    def _building(min_x, min_y, max_x, max_y):
        return {
            "type": "Feature",
            "properties": {"building": "yes"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [min_x, min_y], [max_x, min_y], [max_x, max_y],
                [min_x, max_y], [min_x, min_y],
            ]]},
        }

    @classmethod
    def _square_roads(cls):
        return [
            cls._road([[-0.001, 0], [0.001, 0]], name="south"),
            cls._road([[0.001, 0], [0.001, 0.001]], name="east"),
            cls._road([[0.001, 0.001], [-0.001, 0.001]], name="north"),
            cls._road([[-0.001, 0.001], [-0.001, 0]], name="west"),
        ]

    def test_closed_road_face_defines_block_and_contains_buildings(self):
        features = self._square_roads() + [
            self._building(-0.0005, 0.0002, -0.0003, 0.0004),
            self._building(0.0002, 0.0002, 0.0005, 0.0005),
            self._building(0.002, 0.002, 0.0022, 0.0022),
        ]
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        self.assertEqual(len(result["city_blocks"]), 1)
        block = result["city_blocks"][0]
        self.assertEqual(block["buildings"], [4, 5])
        self.assertEqual(block["metadata"]["building_count"], 2)
        self.assertEqual(len(block["road_edges"]), 4)
        self.assertEqual(len(block["road_nodes"]), 4)
        self.assertGreater(block["metadata"]["area_meters2"], 20_000)
        self.assertEqual(result["features"][4]["properties"]["city_block_ids"], [1])
        self.assertEqual(result["features"][6]["properties"].get("city_block_ids", []), [])

    def test_open_road_group_does_not_infer_building_hull_as_block(self):
        features = [
            self._road([[-0.001, 0], [0.001, 0]]),
            self._building(-0.0005, 0.0002, -0.0003, 0.0004),
            self._building(0.0002, 0.0002, 0.0005, 0.0005),
        ]
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        self.assertEqual(result["city_blocks"], [])
        self.assertEqual(result["city_block_summary"]["block_count"], 0)

    def test_bridge_edge_does_not_close_at_grade_block(self):
        features = self._square_roads()
        features[2]["properties"]["bridge"] = "yes"
        features.append(self._building(-0.0002, 0.0002, 0.0002, 0.0004))
        result = annotate_collection({"type": "FeatureCollection", "features": features})
        self.assertEqual(result["city_blocks"], [])

    def test_block_local_grid_and_road_metadata_are_stable(self):
        features = self._square_roads() + [self._building(-0.0002, 0.0002, 0.0002, 0.0004)]
        first = annotate_collection({"type": "FeatureCollection", "features": features})
        second = annotate_collection({"type": "FeatureCollection", "features": features})
        block = first["city_blocks"][0]
        self.assertEqual(first["city_blocks"], second["city_blocks"])
        self.assertEqual(block["metadata"]["road_classes"], ["residential"])
        self.assertEqual(block["metadata"]["building_count"], 1)
        self.assertIsNotNone(block["local_grid"])


if __name__ == "__main__":
    unittest.main()
