"""JSON diagnostics with explicit source, building, project and world frames."""


def debug_document(records, projects):
    by_grid = {p["local_grid_id"]: p for p in projects}
    buildings = []
    for record in records:
        item = {key: record.get(key) for key in (
            "source_feature_index", "source_feature_id", "status", "stage", "reason",
            "local_grid_id", "local_grid_angle", "levels", "footprint_dimensions",
            "warnings", "errors", "tbx_path", "pz_position")}
        item["source_footprint_lon_lat"] = record.get("source_geometry")
        fp = record.get("footprint")
        item["raster"] = fp
        item["entrances"] = []
        item["stair_core"] = None
        if record["status"] == "generated":
            project = by_grid[record["local_grid_id"]]
            lot = record["placement"]
            item["worlded"] = {"project": project["pzw_path"], "world_origin_cells": project["world_origin_cells"],
                                "cell": lot["cell"], "lot_offset": lot["offset"], "level": 0}
            item["pz_bounding_box"] = [lot["x"], lot["y"], lot["x"] + lot["width"], lot["y"] + lot["height"]]
            item["local_grid_frame"] = {key: project[key] for key in (
                "frame_origin_lon_lat", "frame_origin_uv_meters", "projection_reference_latitude",
                "project_origin_in_frame_tiles", "tile_size_meters", "bearing_degrees")}
            for floor, plan in enumerate(record["plan"]["floors"]):
                for opening in plan["openings"]:
                    if opening["kind"] == "door":
                        item["entrances"].append({"building_tiles": [opening["x"], opening["y"], floor],
                                                  "project_tiles": [lot["x"] + opening["x"], lot["y"] + opening["y"], floor],
                                                  "direction": opening["direction"]})
            core = record["plan"]["core"]
            if core:
                item["stair_core"] = {"building_tiles": core,
                                      "project_tiles": {**core, "x": core["x"] + lot["x"], "y": core["y"] + lot["y"]},
                                      "floors": list(range(record["levels"]))}
        buildings.append(item)
    return {"version": 1, "coordinate_systems": {
        "source_footprint_lon_lat": "GeoJSON longitude/latitude of the validated compiler footprint",
        "raster.footprint": "polygon in cropped building-local tiles; raster.position is its LocalGrid-frame origin",
        "raster.mask": "row-major binary occupancy; tile Y increases south; polygon holes remain empty",
        "pz_bounding_box": "project tiles [minX,minY,maxX,maxY], exclusive maxima",
        "worlded": "300-tile source cells; lot offset is relative to its owning cell",
        "pz_position.world_tiles": "world_origin_cells * 300 + project_tiles; independent LocalGrid packing, not geography",
    }, "buildings": buildings}
