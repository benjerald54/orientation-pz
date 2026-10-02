"""Stair access and independently specified export structure regressions."""

from collections import deque
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from pz_generation import GenerationResult, generate_buildings
from pz_plan import Furniture, Opening, Stair, build_plan
from pz_tbx import render_tbx
from pz_world import LotPlacement, render_pzw
from test_pz_generation import feature, footprint, geometry, grid, rectangle


def load_structural_fixture():
    spec = json.loads((Path(__file__).parent / "tests/fixtures/pz_multistorey.json").read_text())
    grids = []
    for item in spec["grids"]:
        local_grid = grid(item["id"], item.get("bearing_degrees", 0))
        ring = geometry([item["domain"]], angle=local_grid.bearing_degrees)["coordinates"][0]
        domain = [local_grid.projection.forward(*point) for point in ring]
        grids.append(replace(local_grid, domain_geometry=domain))
    bearings = {g.grid_id: g.bearing_degrees for g in grids}
    features = []
    for item in spec["buildings"]:
        features.append({**feature(local_grid_id=item["grid_id"], **{"building:levels": item["levels"]}),
                         "geometry": geometry([rectangle(*item["rectangle_meters"])],
                                              angle=bearings[item["grid_id"]])})
    return spec, generate_buildings(features, range(len(features)), grids, furnish=True)


def reachable(start, cells):
    seen, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        for adjacent in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if adjacent in cells and adjacent not in seen:
                seen.add(adjacent)
                queue.append(adjacent)
    return seen


class StairTests(unittest.TestCase):
    def assert_storeys_connected(self, levels, width=8, height=12):
        building = build_plan(footprint(((1,) * width,) * height), levels=levels, furnish=True)
        core = building.core
        self.assertIsNotNone(core)
        expected_stair = core.stair
        root = ET.fromstring(render_tbx(building))
        entries = root.findall("tile_entry")
        self.assertEqual(entries[int(root.get("Stairs")) - 1].get("category"), "stairs")
        self.assertEqual({t.get("enum") for t in entries[int(root.get("Stairs")) - 1]},
                         {"North1", "North2", "North3", "West1", "West2", "West3"})
        floors = root.findall("floor")
        self.assertEqual(len(floors), levels + 1)
        self.assertEqual(len(root.findall(".//object[@type='stairs']")), levels - 1)
        for level, plan in enumerate(building.floors):
            furniture = {(obj.x, obj.y) for obj in plan.furniture}
            self.assertFalse(core.cells & furniture)
            # Simulate the reader's three-cell opening above a stair. A parallel
            # walkable lane must connect the lower and upper landings on each floor.
            occupied = {(x, y) for y, row in enumerate(plan.grid) for x, value in enumerate(row) if value}
            walkable = occupied - set(expected_stair.steps) - furniture
            door = building.floors[0].openings[0]
            start = (door.x, door.y)
            if start not in occupied:
                start = (door.x - 1, door.y) if door.direction == "W" else (door.x, door.y - 1)
            reached = reachable(start, walkable)
            self.assertEqual(reached, walkable)
            self.assertTrue(set(expected_stair.cells[::4]) <= reached)
            for x, y in core.cells:
                self.assertTrue(0 <= x < width and 0 <= y < height)
                self.assertEqual(plan.grid[y][x], 1)
            stairs = floors[level].findall("object[@type='stairs']")
            if level == levels - 1:
                self.assertFalse(stairs)
                self.assertFalse(plan.stairs)
                continue
            self.assertEqual(len(stairs), 1)
            obj = stairs[0]
            self.assertEqual((int(obj.get("x")), int(obj.get("y")), obj.get("dir")),
                             (expected_stair.x, expected_stair.y, expected_stair.direction))
            self.assertEqual(obj.get("Tile"), root.get("Stairs"))
            next_grid = [int(v) for v in floors[level + 1].findtext("rooms").split(",")]
            for x, y in expected_stair.cells:
                self.assertEqual(next_grid[y * width + x], level + 2)
        self.assertFalse(floors[-1].findall("object"))
        self.assertEqual(set(int(v) for v in floors[-1].findtext("rooms").split(",")), {0})

    def test_two_storeys(self):
        self.assert_storeys_connected(2)

    def test_three_storeys_west_facing(self):
        self.assert_storeys_connected(3, 12, 4)

    def test_four_storeys(self):
        self.assert_storeys_connected(4)

    def test_minimum_core_has_clear_landings_on_every_floor(self):
        for width, height in ((3, 6), (6, 3)):
            with self.subTest(width=width):
                self.assert_storeys_connected(4, width, height)

    def test_core_inside_concave_and_holey_footprints(self):
        for mask in (
            tuple((1,) * 8 if y < 4 else (1,) * 4 + (0,) * 4 for y in range(10)),
            tuple(tuple(int(not (3 <= x <= 4 and 3 <= y <= 5)) for x in range(8)) for y in range(10)),
        ):
            plan = build_plan(footprint(mask), levels=4, furnish=True)
            self.assertTrue(all(mask[y][x] for x, y in plan.core.cells))
            self.assertEqual(len(ET.fromstring(render_tbx(plan)).findall(".//object[@type='stairs']")), 3)

    def test_no_core_is_reported_without_reducing_requested_levels(self):
        for mask in (((1,) * 5,) * 5, ((1,) * 2,) * 8):
            with self.assertRaisesRegex(ValueError, "no valid stair core"):
                build_plan(footprint(mask), levels=2)
        source = feature(**{"building:levels": 4})
        source["geometry"] = geometry([rectangle(w=2, h=8)])
        generated = generate_buildings([source], [0], [grid()])
        self.assertEqual(set(generated.files), {"debug.json"})
        self.assertEqual(generated.report["buildings_generated"], 0)
        self.assertIn("stair core", generated.report["skipped"][0]["reason"])

    def test_single_storey_has_no_stairs_or_reserved_core(self):
        plan = build_plan(footprint([[1, 1], [1, 1]]))
        self.assertIsNone(plan.core)
        self.assertFalse(ET.fromstring(render_tbx(plan)).findall(".//object[@type='stairs']"))

    def test_invalid_stairs_core_furniture_and_openings_are_rejected(self):
        plan = build_plan(footprint(((1,) * 8,) * 12), levels=3)
        first, second, top = plan.floors
        s = first.stairs[0]
        invalid_floors = [
            (replace(first, stairs=()), second, top),
            (first, replace(second, stairs=(replace(s, x=s.x + 1),)), top),
            (first, second, replace(top, stairs=(s,))),
            (replace(first, stairs=(replace(s, direction="E"),)), second, top),
            (replace(first, stairs=(Stair(8, 0, "N"),)), second, top),
            (replace(first, stairs=(Stair(1, 8, "N"),)), second, top),
            (replace(first, stairs=(replace(s, x=1.5),)), second, top),
            (first, second, replace(top, furniture=(Furniture(s.x, s.y, "chair"),))),
        ]
        for floors in invalid_floors:
            with self.subTest(floors=floors), self.assertRaises(ValueError):
                render_tbx(replace(plan, floors=floors))
        with self.assertRaisesRegex(ValueError, "core"):
            render_tbx(replace(plan, core=None))
        bad_grid = [list(row) for row in second.grid]
        bad_grid[s.y][s.x] = 0
        with self.assertRaisesRegex(ValueError, "core"):
            render_tbx(replace(plan, floors=(first, replace(second, grid=tuple(map(tuple, bad_grid))), top)))
        tiny = build_plan(footprint(((1,) * 3,) * 6), levels=2)
        first = replace(tiny.floors[0], openings=(Opening("door", 1, 6, "N"),))
        with self.assertRaisesRegex(ValueError, "opening overlaps"):
            render_tbx(replace(tiny, floors=(first, tiny.floors[1])))


class StructuralFixtureTests(unittest.TestCase):
    def test_fixture_matches_exported_project_and_building_structure(self):
        spec, generated = load_structural_fixture()
        self.assertEqual(generated.report["buildings_generated"], 3)
        self.assertFalse(generated.report["skipped"])
        expected_files = {"manifest.json", "debug.json"}
        resolved_references = set()
        with tempfile.TemporaryDirectory(prefix="PZ fixture & spaces ") as directory:
            destination = Path(directory)
            generated.write(destination)
            report = json.loads((destination / "manifest.json").read_text())
            self.assertEqual(len(report["projects"]), len(spec["expected_projects"]))
            for expected, project in zip(spec["expected_projects"], report["projects"]):
                self.assertEqual(project["local_grid_id"], expected["grid_id"])
                self.assertEqual(project["bearing_degrees"],
                                 next(g.get("bearing_degrees", 0) for g in spec["grids"] if g["id"] == expected["grid_id"]))
                self.assertEqual(project["pzw_path"], expected["pzw_path"])
                expected_files.add(expected["pzw_path"])
                pzw_path = destination / expected["pzw_path"]
                pzw = ET.parse(pzw_path).getroot()
                self.assertEqual([int(pzw.get(key)) for key in ("width", "height")], expected["cells"])
                cells = {(int(c.get("x")), int(c.get("y"))) for c in pzw.findall("cell")}
                self.assertEqual(cells, {(x, y) for y in range(expected["cells"][1])
                                        for x in range(expected["cells"][0])})
                self.assertEqual(len(pzw.findall(".//lot")), len(expected["lots"]))
                self.assertEqual(len(project["buildings"]), len(expected["lots"]))
                for exp, building in zip(expected["lots"], project["buildings"]):
                    self.assertEqual(building["source_feature_index"], exp["source_index"])
                    cx, cy = exp["cell"]
                    lot = pzw.find(f"cell[@x='{cx}'][@y='{cy}']/lot[@map='{exp['map']}']")
                    self.assertIsNotNone(lot)
                    self.assertEqual(lot.get("level"), "0")
                    offset = [int(lot.get(key)) for key in ("x", "y")]
                    self.assertEqual(offset, exp["offset"])
                    self.assertEqual([cx * 300 + offset[0], cy * 300 + offset[1]], exp["tile_position"])
                    self.assertEqual([building["placement"][key] for key in ("x", "y")], exp["tile_position"])
                    self.assertEqual(building["placement"]["cell"], exp["cell"])
                    self.assertEqual(building["placement"]["offset"], offset)
                    self.assertEqual(building["placement"]["tbx_path"], exp["map"])
                    tbx_path = (pzw_path.parent / lot.get("map")).resolve()
                    self.assertTrue(tbx_path.is_relative_to(pzw_path.parent.resolve()))
                    self.assertNotIn(tbx_path, resolved_references)
                    resolved_references.add(tbx_path)
                    expected_files.add(str(tbx_path.relative_to(destination)))
                    tbx = ET.parse(tbx_path).getroot()
                    self.assertEqual(tbx.get("version"), "4")
                    dimensions = [int(tbx.get(key)) for key in ("width", "height")]
                    self.assertEqual(dimensions, exp["dimensions"])
                    self.assertEqual([int(lot.get(key)) for key in ("width", "height")], dimensions)
                    self.assertEqual([building["plan"][key] for key in ("width", "height")], dimensions)
                    self.assertEqual([building["footprint"][key] for key in ("width", "height")], dimensions)
                    self.assertEqual([building["plan"]["core"][key] for key in ("x", "y", "width", "height")], exp["core"])
                    floors = tbx.findall("floor")
                    self.assertEqual(len(floors), len(exp["floor_room_ids"]))
                    self.assertEqual(len(tbx.findall("room")), len(floors) - 1)
                    actual_stairs = []
                    for level, (floor, room_id) in enumerate(zip(floors, exp["floor_room_ids"])):
                        self.assertEqual([int(v) for v in floor.findtext("rooms").split(",")],
                                         [room_id] * (dimensions[0] * dimensions[1]))
                        for stair in floor.findall("object[@type='stairs']"):
                            actual_stairs.append([level, int(stair.get("x")), int(stair.get("y")), stair.get("dir")])
                            entry = tbx.findall("tile_entry")[int(stair.get("Tile")) - 1]
                            self.assertEqual(entry.get("category"), "stairs")
                        roofs = floor.findall("object[@type='roof']")
                        self.assertEqual(len(roofs), int(level == exp["roof_floor"]))
                        if roofs:
                            self.assertEqual([int(roofs[0].get(key)) for key in ("x", "y", "width", "height")], [0, 0, *dimensions])
                            self.assertEqual((roofs[0].get("RoofType"), roofs[0].get("Depth")), ("FlatTop", "Three"))
                    self.assertEqual(actual_stairs, exp["stairs"])
                    for kind in ("door", "window"):
                        objects = tbx.findall(f".//object[@type='{kind}']")
                        self.assertEqual(len(objects), (len(floors) - 1 if kind == "window" else 1) if exp[kind] else 0)
                        for obj in objects:
                            self.assertEqual([int(obj.get("x")), int(obj.get("y")), obj.get("dir")], exp[kind])
                    self.assertFalse(tbx.findall(".//object[@type='wall']"))
                    self.assertEqual(len(tbx.findall(".//object[@type='furniture']")), exp["furniture_count"])
            actual_files = {str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()}
            self.assertEqual(actual_files, expected_files)

    def test_invalid_reference_paths_and_dimensions(self):
        for path in ("../other/building.tbx", "/absolute.tbx", "C:/building.tbx", "buildings\\x.tbx", "a.xml"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                render_pzw([LotPlacement(path, 0, 0, 10, 10)], 300, 300)
        for width, height in ((0, 10), (10, 301), (1.5, 10)):
            with self.assertRaises(ValueError):
                render_pzw([LotPlacement("building.tbx", 0, 0, width, height)], 600, 600)


class ExportFailureTests(unittest.TestCase):
    def assert_failed_export_preserves_previous(self, fail_during_publish, fail_name="world.pzw",
                                               failure_type=OSError, after_replace=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = GenerationResult({"generation": "old"}, {"buildings/a.tbx": "old TBX", "world.pzw": "old PZW"})
            old.write(root)
            (root / "notes.txt").write_text("unrelated user file")
            (root / "buildings/stale.tbx").write_text("unreferenced old building")
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            new = GenerationResult({"generation": "new"}, {"buildings/a.tbx": "new TBX",
                                   "buildings/b.tbx": "new building", "world.pzw": "new PZW"})
            original_write, original_replace = Path.write_text, os.replace

            def fail_write(path, *args, **kwargs):
                if path.name == fail_name:
                    raise failure_type("simulated disk write failure")
                return original_write(path, *args, **kwargs)

            def fail_replace(source, destination):
                if Path(source).name == fail_name and Path(source).parent.name == "new":
                    if after_replace:
                        original_replace(source, destination)
                    raise failure_type("simulated publish failure")
                return original_replace(source, destination)

            target, replacement = ("pz_generation.os.replace", fail_replace) if fail_during_publish else (
                "pathlib.Path.write_text", fail_write)
            with patch(target, replacement), self.assertRaises(failure_type):
                new.write(root)
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(after, before)
            new.write(root)
            self.assertEqual((root / "notes.txt").read_text(), "unrelated user file")
            self.assertEqual((root / "buildings/stale.tbx").read_text(), "unreferenced old building")
            self.assertEqual((root / "buildings/a.tbx").read_text(), "new TBX")
            self.assertEqual(json.loads((root / "manifest.json").read_text()), {"generation": "new"})
            self.assertFalse(list(root.glob(".pz-export-*")))

    def test_failed_staging_preserves_previous_export(self):
        self.assert_failed_export_preserves_previous(False)

    def test_failed_publish_rolls_back_previous_files_and_removes_new_files(self):
        for name in ("world.pzw", "manifest.json"):
            with self.subTest(name=name):
                self.assert_failed_export_preserves_previous(True, name)

    def test_interrupted_publication_rolls_back_and_reraises(self):
        for failure_type in (RuntimeError, KeyboardInterrupt, SystemExit):
            with self.subTest(failure_type=failure_type):
                self.assert_failed_export_preserves_previous(True, failure_type=failure_type)

    def test_interruption_after_rename_restores_the_just_replaced_file(self):
        for name in ("world.pzw", "manifest.json"):
            with self.subTest(name=name):
                self.assert_failed_export_preserves_previous(
                    True, name, failure_type=KeyboardInterrupt, after_replace=True)

    def test_invalid_export_paths_do_not_write_outside_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("../escape.tbx", "/absolute.tbx", "C:/outside.tbx", "a\\b.tbx"):
                with self.assertRaises(ValueError):
                    GenerationResult({}, {name: "bad path"}).write(directory)
            self.assertFalse(list(Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
