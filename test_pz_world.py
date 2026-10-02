"""Source-format checks; these do not execute WorldEd or verify game behavior."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from pz_generation import generate_buildings
from pz_world import LotPlacement, bind_project_paths, render_pzw
from test_pz_generation import feature, grid
from test_pz_stairs import load_structural_fixture


def structure(node):
    return node.tag, node.attrib, [structure(child) for child in node]


class WorldFormatTests(unittest.TestCase):
    def test_matches_actual_knoxmap_writer_fixture(self):
        reference = ET.parse(Path(__file__).parent / "tests/fixtures/knoxmap_world.pzw").getroot()
        document = render_pzw([LotPlacement("buildings/a&b.tbx", 299, 301, 10, 8)],
                              600, 600, terrain_bmp="terrain & base.bmp")
        with tempfile.TemporaryDirectory(prefix="WorldEd & paths ") as directory:
            actual = ET.fromstring(bind_project_paths(document, directory))
            # Normalize only destination-specific paths, not schema/values.
            for selector in ("BMPToTMX/tmxexportdir", "GenerateLots/exportdir"):
                node = actual.find(selector)
                relative = Path(node.get("path")).relative_to(Path(directory).resolve())
                node.set("path", "/PROJECT/" + relative.as_posix())
            self.assertEqual(actual.attrib, reference.attrib)
            for tag in ("BMPToTMX", "TMXToBMP", "GenerateLots", "LuaSettings", "bmp"):
                self.assertEqual(structure(actual.find(tag)), structure(reference.find(tag)))
            # Cell enumeration order is irrelevant; lot order is retained.
            for root in (actual, reference):
                cells = sorted(root.findall("cell"), key=lambda c: (int(c.get("x")), int(c.get("y"))))
                if root is actual:
                    actual_cells = [structure(cell) for cell in cells]
                else:
                    self.assertEqual(actual_cells, [structure(cell) for cell in cells])
            self.assertEqual(len(actual.findall(".//lot")), 1)  # spans x=299..308

    def test_no_terrain_input_does_not_invent_resource_references(self):
        result = generate_buildings([feature()], [0], [grid()])
        with tempfile.TemporaryDirectory() as directory:
            before = dict(result.files)
            result.write(directory)
            self.assertEqual(result.files, before)
            project = Path(directory) / "grid_0"
            root = ET.parse(project / "world.pzw").getroot()
            self.assertIsNone(root.find("bmp"))
            self.assertTrue(all(c.get("map") == "" for c in root.findall("cell")))
            self.assertEqual(root.find("GenerateLots/ZombieSpawnMap").get("path"), "")
            for selector, folder in (("BMPToTMX/tmxexportdir", "tmx"), ("GenerateLots/exportdir", "lots")):
                path = Path(root.find(selector).get("path"))
                self.assertEqual(path, project / folder)
                self.assertTrue(path.is_absolute() and path.is_dir())
            self.assertEqual(root.find(".//lot").get("map"), "buildings/building_0.tbx")

    def test_relative_and_absolute_terrain_paths_and_tmx_naming(self):
        with tempfile.TemporaryDirectory(prefix="WorldEd & paths ") as directory:
            project = Path(directory).resolve() / "grid_0"
            (project / "tmx").mkdir(parents=True)
            # Synthetic file contents: only filename/reference behavior is tested.
            expected = project / "tmx/terrain & base_91_4.tmx"
            expected.write_text("synthetic fixture; not a parsed TMX")
            (project / "tmx/terrain & base_0_0.tmx").write_text("wrong origin")
            for terrain in ("terrain & base.bmp", (project / "terrain & base.bmp").as_posix()):
                result = generate_buildings([feature()], [0], [grid()], world_origin=(91, 4),
                                            terrain_bmps={0: terrain})
                result.write(directory)
                root = ET.parse(project / "world.pzw").getroot()
                self.assertEqual(root.find("bmp").attrib,
                                 {"path": terrain, "x": "0", "y": "0", "width": "1", "height": "1"})
                self.assertEqual(root.find("cell").get("map"), expected.as_posix())
                self.assertEqual(root.find("GenerateLots/worldOrigin").get("origin"), "91,4")
            expected.unlink()
            result.write(directory)
            self.assertEqual(ET.parse(project / "world.pzw").find("cell").get("map"), "")

    def test_multigrid_world_regions_are_disjoint_in_source_and_compiled_cells(self):
        _, result = load_structural_fixture()
        first, second = result.report["projects"]
        self.assertEqual(first["world_origin_cells"], [70, 0])
        self.assertEqual(second["world_origin_cells"], [75, 0])
        self.assertEqual(second["bearing_degrees"], 30)
        occupied = {300: set(), 256: set()}
        with tempfile.TemporaryDirectory() as directory:
            result.write(directory)
            for p in result.report["projects"]:
                root = ET.parse(Path(directory) / p["pzw_path"]).getroot()
                ox, oy = p["world_origin_cells"]
                width, height = p["width_cells"], p["height_cells"]
                self.assertEqual([int(root.get(k)) for k in ("width", "height")], [width, height])
                self.assertEqual(root.find("GenerateLots/worldOrigin").get("origin"), f"{ox},{oy}")
                for size, used in occupied.items():
                    cells = {(x, y) for x in range(ox * 300 // size, ((ox + width) * 300 - 1) // size + 1)
                             for y in range(oy * 300 // size, ((oy + height) * 300 - 1) // size + 1)}
                    self.assertFalse(used & cells)
                    used.update(cells)
                for building in p["buildings"]:
                    lot = building["placement"]
                    self.assertLessEqual(lot["x"] + lot["width"], width * 300)
                    self.assertLessEqual(lot["y"] + lot["height"], height * 300)
        # This is allocation in an abstract world, not geographic reassembly.
        self.assertEqual(first["buildings"][0]["placement"]["offset"], [298, 1])

    def test_allocation_uses_full_shifted_project_extent_and_sorted_grid_ids(self):
        wide = replace(grid(5), domain_geometry=[(-20, -20), (620, -20), (620, 100), (-20, 100), (-20, -20)])
        grids = [grid(9), wide, grid(1)]
        features = [feature(-330, local_grid_id=5), feature(local_grid_id=9), feature(local_grid_id=1)]
        result = generate_buildings(features, range(3), grids, world_origin=(100, -7))
        projects = result.report["projects"]
        self.assertEqual([p["local_grid_id"] for p in projects], [1, 5, 9])
        self.assertEqual([p["world_origin_cells"] for p in projects], [[100, -7], [103, -7], [109, -7]])
        self.assertEqual(projects[1]["project_origin_in_frame_tiles"][0], -310)
        self.assertEqual(projects[1]["width_cells"], 4)
        again = generate_buildings(features, range(3), list(reversed(grids)), world_origin=(100, -7))
        self.assertEqual(result.files, again.files)
        self.assertEqual(result.report, again.report)

    def test_invalid_origin_is_rejected_even_without_buildings(self):
        for origin in ((0,), (0, 1, 2), (True, 0), (1.5, 0), ("70", 0)):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                generate_buildings([], [], [], world_origin=origin)


if __name__ == "__main__":
    unittest.main()
