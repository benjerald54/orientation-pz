"""Golden, corruption, stress and end-to-end semantic export checks."""

import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from pz_generation import generate_buildings
from pz_tbx import render_tbx
from pz_plan import build_plan
from pz_validate import SemanticValidationError, validate_directory, validate_export, validate_pzw, validate_tbx
from test_pz_generation import feature, footprint, geometry, grid, rectangle, fixture
from test_pz_stairs import load_structural_fixture


def serialize(root):
    return ET.tostring(root, encoding="unicode")


class SemanticValidatorTests(unittest.TestCase):
    def setUp(self):
        self.document = render_tbx(build_plan(footprint(((1,) * 8,) * 12), levels=4, furnish=True))

    def test_tbx_mutations_are_rejected(self):
        def room(root):
            node = root.find("floor/rooms")
            node.text = "999," + node.text.split(",", 1)[1]

        def move(root, kind, **values):
            root.find(f".//object[@type='{kind}']").attrib.update(values)

        def drop_stair(root):
            floor = root.findall("floor")[1]
            floor.remove(floor.find("object[@type='stairs']"))

        def orphan(root):
            ET.SubElement(root, "object", type="door", x="0", y="0", dir="N")

        def door_furniture(root):
            door = root.find("floor/object[@type='door']")
            x, y = int(door.get("x")), int(door.get("y"))
            if y == 12:
                y -= 1
            move(root, "furniture", x=str(x), y=str(y))

        mutations = {
            "room id": room,
            "interior door": lambda r: move(r, "door", x="4", y="4"),
            "interior window": lambda r: move(r, "window", x="4", y="4"),
            "missing flight": drop_stair,
            "shifted flight": lambda r: r.findall("floor")[1].find("object[@type='stairs']").set("x", "4"),
            "invalid direction": lambda r: move(r, "stairs", dir="E"),
            "furniture outside": lambda r: move(r, "furniture", x="8"),
            "furniture core": lambda r: move(r, "furniture", x="3", y="4"),
            "furniture door": door_furniture,
            "furniture index": lambda r: move(r, "furniture", FurnitureTiles="999"),
            "used tile index": lambda r: setattr(r.find("used_tiles"), "text", "999"),
            "used furniture index": lambda r: setattr(r.find("used_furniture"), "text", "999"),
            "wrong opening tile category": lambda r: move(r, "door", Tile="1"),
            "roof hole": lambda r: move(r, "roof", width="7"),
            "orphan object": orphan,
            "roof support object": lambda r: ET.SubElement(r.findall("floor")[-1], "object", type="door"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                root = ET.fromstring(self.document)
                mutate(root)
                with self.assertRaises(SemanticValidationError):
                    validate_tbx(serialize(root))

    def test_window_to_enclosed_hole_and_furniture_across_room_wall_fail(self):
        mask = tuple(tuple(int(not (3 <= x < 5 and 3 <= y < 5)) for x in range(8)) for y in range(8))
        root = ET.fromstring(render_tbx(build_plan(footprint(mask))))
        window = root.find("floor/object[@type='window']")
        window.attrib.update(x="3", y="3", dir="N")
        with self.assertRaisesRegex(SemanticValidationError, "window_exterior"):
            validate_tbx(serialize(root))
        root = ET.fromstring(self.document)
        obj = root.find("floor/object[@type='furniture']")
        obj.attrib.update(x="0", y="0")
        definition = root.findall("furniture")[int(obj.get("FurnitureTiles"))].find("entry")
        ET.SubElement(definition, "tile", x="1", y="0", name="test_tile")
        values = root.findtext("floor/rooms").split(",")
        values[1] = "2"  # two occupied cells separated by a room wall
        root.find("floor/rooms").text = ",".join(values)
        with self.assertRaisesRegex(SemanticValidationError, "furniture_wall_overlap"):
            validate_tbx(serialize(root))

    def test_pzw_corruption_and_cross_cell_overlap(self):
        _, result = load_structural_fixture()
        project = result.report["projects"][0]
        document = result.files[project["pzw_path"]]
        prefix = str(Path(project["pzw_path"]).parent)
        read = lambda path: result.files[f"{prefix}/{path}"]
        self.assertEqual(len(validate_pzw(document, read)["lots"]), 2)

        def duplicate_cell(root):
            root.append(copy.deepcopy(root.find("cell")))

        def crossing_overlap(root):
            lot = copy.deepcopy(root.find(".//lot"))
            lot.set("x", "0")
            # Original spans x=298..305, so the overlap is across owning cells.
            root.find("cell[@x='1'][@y='1']").append(lot)

        for mutate in (
            duplicate_cell, crossing_overlap,
            lambda r: r.find(".//lot").set("x", "300"),
            lambda r: r.find(".//lot").set("width", "9"),
            lambda r: r.find(".//lot").set("map", "buildings/missing.tbx"),
            lambda r: r.find(".//lot").set("map", "../grid_9/buildings/building_2.tbx"),
            lambda r: r.find("cell").set("x", "3"),
            lambda r: r.remove(r.findall("cell")[-1]),
        ):
            root = ET.fromstring(document)
            mutate(root)
            with self.assertRaises(SemanticValidationError):
                validate_pzw(serialize(root), read)

    def test_manifest_origin_position_and_debug_corruption_fail(self):
        _, result = load_structural_fixture()
        for field in ("world_origin_cells", "pz_position", "tbx_path", "levels"):
            bad = copy.deepcopy(result)
            if field == "world_origin_cells":
                bad.report["projects"][1][field][0] = 70
            else:
                bad.report["buildings"][0][field] = None
            with self.subTest(field=field), self.assertRaises(SemanticValidationError):
                validate_export(bad.report, bad.files)
        bad = copy.deepcopy(result)
        bad.files["debug.json"] = "{}"
        with self.assertRaisesRegex(SemanticValidationError, "debug_document"):
            validate_export(bad.report, bad.files)

    def test_bad_output_is_blocked_before_publishing(self):
        _, result = load_structural_fixture()
        with tempfile.TemporaryDirectory() as directory:
            result.write(directory)
            before = {str(p.relative_to(directory)): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
            del result.files["grid_7/buildings/building_0.tbx"]
            with self.assertRaises(SemanticValidationError):
                result.write(directory)
            after = {str(p.relative_to(directory)): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_terrain_cell_references_and_cross_grid_symlinks(self):
        result = generate_buildings([feature()], [0], [grid()], terrain_bmps={0: "terrain.bmp"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tmx = root / "grid_0/tmx/terrain_70_0.tmx"
            tmx.parent.mkdir(parents=True)
            tmx.write_text("synthetic TMX; contents not validated")
            result.write(root)
            self.assertEqual(validate_directory(root)["buildings"], 1)
            tmx.unlink()
            with self.assertRaisesRegex(SemanticValidationError, "missing_terrain_map"):
                validate_directory(root)
            result.write(root)  # absent TMX now clears the reference
            path = root / result.report["buildings"][0]["tbx_path"]
            other = root / "grid_99"
            other.mkdir()
            target = other / "foreign.tbx"
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
            with self.assertRaisesRegex(SemanticValidationError, "resource_path"):
                validate_directory(root)

    def test_export_rejects_symlink_destinations_before_any_writes(self):
        _, result = load_structural_fixture()
        for name in ("grid_7", "grid_7/buildings", "grid_7/tmx", "grid_7/lots", "manifest.json"):
            for outside in (False, True):
                with self.subTest(name=name, outside=outside), tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    root = base / "export"
                    root.mkdir()
                    target = (base if outside else root) / "other-grid"
                    if name.endswith(".json"):
                        target.write_text("unchanged")
                    else:
                        target.mkdir()
                        (target / "sentinel").write_text("unchanged")
                    alias = root / name
                    alias.parent.mkdir(parents=True, exist_ok=True)
                    alias.symlink_to(target, target_is_directory=target.is_dir())
                    before = {str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*") if p.is_file() and not p.is_symlink()}
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        result.write(root)
                    after = {str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*") if p.is_file() and not p.is_symlink()}
                    self.assertEqual(after, before)
                    self.assertTrue(alias.is_symlink())
                    self.assertFalse(list(root.glob(".pz-export-*")))


class ManifestAndGoldenTests(unittest.TestCase):
    def test_precise_rejection_and_source_identity(self):
        source = feature(local_grid_id=17, **{"building:levels": 4})
        source["id"] = 1834
        source["geometry"] = geometry([rectangle(w=2.1, h=3.4)])
        result = generate_buildings([source], [0], [grid(17)])
        record = result.report["buildings"][0]
        self.assertEqual({k: record[k] for k in ("source_feature_id", "status", "stage", "reason", "levels", "local_grid_id")},
                         {"source_feature_id": 1834, "status": "rejected", "stage": "pz_layout",
                          "reason": "stair_core_does_not_fit", "levels": 4, "local_grid_id": 17})
        self.assertEqual(record["footprint_dimensions"]["meters"], [2.1, 3.4])
        self.assertTrue(record["errors"])
        debug = json.loads(result.files["debug.json"])["buildings"][0]
        self.assertEqual(debug["source_footprint_lon_lat"], source["geometry"])
        self.assertEqual(debug["raster"], json.loads(json.dumps(record["footprint"])))

    def test_golden_semantic_structures(self):
        spec = json.loads((Path(__file__).parent / "tests/fixtures/pz_golden.json").read_text())
        for case in spec["cases"]:
            with self.subTest(case=case["name"]):
                source = feature(**{"building:levels": case["levels"]})
                source["id"] = case["name"]
                source["geometry"] = geometry([case["ring"]], angle=case["angle"])
                result = generate_buildings([source], [0], [grid(angle=case["angle"])], furnish=True)
                self.assertEqual(result.report["buildings_generated"], 1, result.report["skipped"])
                record = result.report["buildings"][0]
                semantics = validate_tbx(result.files[record["tbx_path"]])
                self.assertEqual({k: semantics[k] for k in case["expected"]}, case["expected"])
                if "placement" in case:
                    self.assertEqual({k: record["placement"][k] for k in case["placement"]}, case["placement"])
                self.assertEqual(record["source_feature_id"], case["name"])
                self.assertEqual(record["local_grid_angle"], case["angle"])
                debug = json.loads(result.files["debug.json"])["buildings"][0]
                self.assertEqual(debug["worlded"]["cell"], record["placement"]["cell"])
                self.assertTrue(debug["entrances"])
                self.assertEqual(bool(debug["stair_core"]), case["levels"] > 1)
                # XML whitespace/attribute order is not part of the semantic oracle.
                root = ET.fromstring(result.files[record["tbx_path"]])
                root.attrib = dict(reversed(list(root.attrib.items())))
                self.assertEqual(validate_tbx(serialize(root)), semantics)

    def test_disk_validator_and_cli(self):
        _, result = load_structural_fixture()
        with tempfile.TemporaryDirectory() as directory:
            result.write(directory)
            self.assertEqual(validate_directory(directory)["buildings"], 3)
            run = subprocess.run([sys.executable, "-m", "pz_validate", directory], cwd=Path(__file__).parent,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertFalse(json.loads(run.stdout)["editor_verified"])
            (Path(directory) / "grid_7/buildings/building_0.tbx").unlink()
            run = subprocess.run([sys.executable, "-m", "pz_validate", directory], cwd=Path(__file__).parent,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(json.loads(run.stdout)["status"], "failed")

    def test_whole_compiler_determinism_in_fresh_processes(self):
        source = fixture()
        for i, f in enumerate(source["features"][:3]):
            f["id"] = f"source-{i}"
            f["properties"]["building:levels"] = i + 2
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            input_path, destination = base / "input.json", base / "export"
            input_path.write_text(json.dumps(source))
            snapshots = []
            for seed in ("17", "9001"):
                completed = subprocess.run([sys.executable, "orientation_detector.py", str(input_path), str(base / "compiled.json"),
                                            "--pz-output-dir", str(destination), "--pz-furnish"],
                                           env={**os.environ, "PYTHONHASHSEED": seed}, cwd=Path(__file__).parent,
                                           capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(validate_directory(destination)["buildings"], 3)
                snapshots.append(({str(p.relative_to(destination)): p.read_bytes() if p.is_file() else None
                                   for p in destination.rglob("*")}, (base / "compiled.json").read_bytes()))
            self.assertEqual(snapshots[0], snapshots[1])
            self.assertIn("debug.json", snapshots[0][0])
            self.assertIn("manifest.json", snapshots[0][0])

    def test_cli_malformed_manifest_is_a_json_failure(self):
        _, result = load_structural_fixture()
        with tempfile.TemporaryDirectory() as directory:
            result.write(directory)
            path = Path(directory) / "manifest.json"
            report = json.loads(path.read_text())
            report["projects"][0]["project_origin_in_frame_tiles"] = []
            path.write_text(json.dumps(report))
            run = subprocess.run([sys.executable, "-m", "pz_validate", directory], cwd=Path(__file__).parent,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(json.loads(run.stdout)["status"], "failed")
            self.assertFalse(run.stderr)
            for invalid_root in ([], None):
                path.write_text(json.dumps(invalid_root))
                with self.assertRaisesRegex(SemanticValidationError, "manifest_root"):
                    validate_directory(directory)


class StressTests(unittest.TestCase):
    def test_one_thousand_mixed_buildings_never_emit_invalid_output(self):
        grids, features = [], []
        angles = (0, .1, 13.7, 27.3, 44.9, 63.2, 89.3, 110.7, 145.1, 179.9)
        for gid, angle in enumerate(angles):
            base = grid(gid, angle)
            domain = geometry([rectangle(0, 0, 3100, 3100)], angle=angle)["coordinates"][0]
            grids.append(replace(base, domain_geometry=[base.projection.forward(*p) for p in domain]))
            for n in range(100):
                x, y = (n % 10) * 300 + 298, (n // 10) * 300 + 298
                kind = (n + gid) % 10
                rings = [rectangle(x, y, 8, 12)]
                if kind == 0:
                    rings = [rectangle(x, y, .1, .1)]
                elif kind == 1:
                    rings = [rectangle(x, y, 2000, 2000)]
                elif kind == 2:
                    rings = [rectangle(x, y, 301, 2)]
                elif kind == 3:
                    rings = [rectangle(x, y, 2, 12)]
                elif kind == 4:
                    rings = [[(x, y), (x + 12, y), (x + 12, y + 4), (x + 4, y + 4),
                              (x + 4, y + 12), (x, y + 12), (x, y)]]
                elif kind == 5:
                    rings = [rectangle(x, y, 12, 12), rectangle(x + 4, y + 4, 4, 4)]
                elif kind == 6:
                    rings = [rectangle(x, y, 3, 6)]
                source = feature(local_grid_id=gid, **{"building:levels": 1 + (n * 7 + gid) % 10})
                source["id"] = f"stress-{gid}-{n}"
                source["geometry"] = geometry(rings, angle=angle + (11.7 if kind == 7 else 0))
                features.append(source)
        result = generate_buildings(features, range(1000), grids, furnish=True)
        self.assertEqual(len(result.report["buildings"]), 1000)
        self.assertGreater(result.report["buildings_generated"], 400)
        self.assertGreater(result.report["buildings_rejected"], 200)
        self.assertEqual(result.report["buildings_generated"] + result.report["buildings_rejected"], 1000)
        self.assertEqual(validate_export(result.report, result.files)["status"], "passed")
        for record in result.report["buildings"]:
            self.assertIn(record["status"], {"generated", "rejected"})
            if record["status"] == "rejected":
                self.assertTrue(record["errors"])
                self.assertTrue(record["reason"])
                self.assertIsNone(record["tbx_path"])
        with tempfile.TemporaryDirectory() as directory:
            result.write(directory)
            self.assertEqual(validate_directory(directory)["buildings"], result.report["buildings_generated"])


if __name__ == "__main__":
    unittest.main()
