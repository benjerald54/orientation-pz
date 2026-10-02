"""Serialize local plans to BuildingEd TBX v4; no geographic coordinates here.

Tile names and enum categories follow PZ Mapping Tools BuildingTemplates.txt.
The small fixed palette avoids process-dependent variation and external assets.
"""

import xml.etree.ElementTree as ET


def _wall_tiles(sheet, base):
    enums = ("West", "North", "NorthWest", "SouthEast",
             "WestWindow", "NorthWindow", "WestDoor", "NorthDoor")
    return tuple((enum, f"{sheet}_{base + offset:03d}")
                 for enum, offset in zip(enums, (0, 1, 2, 3, 8, 9, 10, 11)))


# Order is part of the file's reference table. Values below are 1-based.
TILE_ENTRIES = (
    ("exterior_walls", _wall_tiles("walls_exterior_house_01", 32)),
    ("interior_walls", _wall_tiles("walls_interior_house_01", 16)),
    ("floors", (("Floor", "floors_interior_tilesandwood_01_041"),)),
    ("ceiling", (("Ceiling", "ceilings_01_000"),)),
    ("doors", tuple((name, f"fixtures_doors_01_{index:03d}") for index, name in
                    enumerate(("West", "North", "WestOpen", "NorthOpen")))),
    ("door_frames", (("West", "fixtures_doors_frames_01_000"),
                     ("North", "fixtures_doors_frames_01_001"))),
    ("windows", (("West", "fixtures_windows_01_000"),
                 ("North", "fixtures_windows_01_001"))),
    ("roof_caps", (("CapGapE3", "walls_exterior_house_01_032"),
                   ("CapGapS3", "walls_exterior_house_01_033"))),
    ("roof_tops", tuple((f"{side}{index}", "roofs_01_054")
                        for side in ("West", "North") for index in (1, 2, 3))),
    ("stairs", tuple((f"{side}{index + 1}", f"fixtures_stairs_01_{base + index:03d}")
                     for side, base in (("West", 0), ("North", 8)) for index in range(3))),
)
EXTERIOR, INTERIOR, FLOOR, CEILING, DOOR, FRAME, WINDOW, ROOF_CAP, ROOF_TOP, STAIRS = range(1, 11)


def element(parent, tag, **attributes):
    return ET.SubElement(parent, tag, {key: str(value) for key, value in attributes.items()})


def xml_document(root):
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _room_csv(grid, offset=0):
    return "\n" + ",\n".join(",".join(str(value + offset if value else 0) for value in row)
                              for row in grid) + "\n"


def _validate(plan):
    w, h = plan.width, plan.height
    if not (1 <= w <= 300 and 1 <= h <= 300 and 1 <= len(plan.floors) <= 30):
        raise ValueError("invalid building dimensions or storey count")
    core = plan.core
    if len(plan.floors) > 1 and core is None:
        raise ValueError("multistorey building requires a stair core")
    if core is not None:
        if (any(type(v) is not int for v in (core.x, core.y, core.width, core.height))
                or (core.width, core.height) not in ((3, 6), (6, 3))
                or core.x < 0 or core.y < 0 or core.x + core.width > w or core.y + core.height > h):
            raise ValueError("invalid stair core bounds")
    reserved = core.cells if core else set()
    flight = set(core.stair.cells) if core else set()
    for level, floor in enumerate(plan.floors):
        if (floor.width, floor.height) != (w, h) or len(floor.grid) != h:
            raise ValueError("floor dimensions do not match building")
        if any(len(row) != w or any(type(v) is not int or not 0 <= v <= len(floor.rooms)
                                   for v in row) for row in floor.grid):
            raise ValueError("invalid room grid")
        if any(not room.name for room in floor.rooms):
            raise ValueError("room name must not be empty")
        if core:
            core_rooms = {floor.grid[y][x] for x, y in reserved}
            if len(core_rooms) != 1 or 0 in core_rooms:
                raise ValueError("stair core must occupy one room on every floor")
        for stair in floor.stairs:
            if stair.direction not in ("N", "W"):
                raise ValueError("invalid stair direction")
            if any(type(v) is not int for v in (stair.x, stair.y)) or any(
                not (0 <= x < w and 0 <= y < h) for x, y in stair.cells
            ):
                raise ValueError("stair flight outside floor bounds")
        expected_stairs = (core.stair,) if core and level < len(plan.floors) - 1 else ()
        if floor.stairs != expected_stairs:
            raise ValueError("stairs must align in the core and connect every occupied storey")
        edges = set()
        for obj in floor.openings:
            if obj.kind not in ("door", "window") or obj.direction not in ("N", "W"):
                raise ValueError("invalid opening kind or direction")
            x, y = obj.x, obj.y
            if type(x) is not int or type(y) is not int:
                raise ValueError("opening coordinates must be integers")
            if not (0 <= x <= w and 0 <= y <= h):
                raise ValueError("opening outside building")

            def cell(cx, cy):
                return floor.grid[cy][cx] if 0 <= cx < w and 0 <= cy < h else 0

            neighbor = cell(x - 1, y) if obj.direction == "W" else cell(x, y - 1)
            if cell(x, y) == neighbor:
                raise ValueError("opening must lie on a room boundary")
            adjoining = {(x, y), (x - 1, y) if obj.direction == "W" else (x, y - 1)}
            if adjoining & flight:
                raise ValueError("opening overlaps stair flight")
            edge = (x, y, obj.direction)
            if edge in edges:
                raise ValueError("overlapping openings")
            edges.add(edge)
        for obj in floor.furniture:
            if (type(obj.x) is not int or type(obj.y) is not int
                    or not (0 <= obj.x < w and 0 <= obj.y < h)
                    or not floor.grid[obj.y][obj.x] or not obj.tile
                    or obj.orientation not in ("N", "W", "S", "E")):
                raise ValueError("invalid furniture placement")
            if (obj.x, obj.y) in reserved:
                raise ValueError("furniture occupies reserved stair core")
    covered = set()
    for rect in plan.roof:
        if (any(type(v) is not int for v in (rect.x, rect.y, rect.width, rect.height))
                or rect.width < 1 or rect.height < 1 or rect.x < 0 or rect.y < 0
                or rect.x + rect.width > w or rect.y + rect.height > h):
            raise ValueError("invalid roof rectangle")
        cells = {(x, y) for y in range(rect.y, rect.y + rect.height)
                 for x in range(rect.x, rect.x + rect.width)}
        if covered & cells:
            raise ValueError("overlapping roof rectangles")
        covered.update(cells)
    expected = {(x, y) for y, row in enumerate(plan.floors[-1].grid)
                for x, value in enumerate(row) if value}
    if covered != expected:
        raise ValueError("roof must cover the top floor exactly")


def render_tbx(plan):
    _validate(plan)
    root = ET.Element("building", {
        "version": "4", "width": str(plan.width), "height": str(plan.height),
        "ExteriorWall": str(EXTERIOR), "ExteriorWallTrim": "0",
        "Door": str(DOOR), "DoorFrame": str(FRAME), "Window": str(WINDOW),
        "Curtains": "0", "Shutters": "0", "Stairs": str(STAIRS) if plan.core else "0",
        "RoofCap": str(ROOF_CAP), "RoofSlope": "0", "RoofTop": str(ROOF_TOP),
        "GrimeWall": "0",
    })
    for category, tiles in TILE_ENTRIES:
        entry = element(root, "tile_entry", category=category)
        for enum, name in tiles:
            element(entry, "tile", enum=enum, tile=name)
    furniture = sorted({(obj.tile, obj.orientation)
                        for floor in plan.floors for obj in floor.furniture})
    for tile, orientation in furniture:
        definition = element(root, "furniture")
        facing = element(definition, "entry", orient=orientation)
        element(facing, "tile", x=0, y=0, name=tile)
    element(root, "used_tiles").text = " ".join(map(str, range(1, len(TILE_ENTRIES) + 1)))
    element(root, "used_furniture").text = " ".join(map(str, range(len(furniture))))
    for floor in plan.floors:
        for room in floor.rooms:
            element(root, "room", Name=room.name, InternalName=room.name, Color="200 200 200",
                    InteriorWall=INTERIOR, InteriorWallTrim=0, Floor=FLOOR,
                    Ceiling=CEILING, GrimeFloor=0, GrimeWall=0)
    offset = 0
    for level, plan_floor in enumerate(plan.floors):
        floor = element(root, "floor")
        for obj in plan_floor.openings:
            attributes = {"FrameTile": FRAME, "Tile": DOOR} if obj.kind == "door" else {
                "CurtainsTile": 0, "ShuttersTile": 0, "Tile": WINDOW}
            element(floor, "object", type=obj.kind, x=obj.x, y=obj.y,
                    dir=obj.direction, **attributes)
        for obj in plan_floor.furniture:
            element(floor, "object", type="furniture", x=obj.x, y=obj.y,
                    orient=obj.orientation, FurnitureTiles=furniture.index((obj.tile, obj.orientation)))
        for stair in plan_floor.stairs:
            element(floor, "object", type="stairs", x=stair.x, y=stair.y,
                    dir=stair.direction, Tile=STAIRS)
        if level == len(plan.floors) - 1:
            for rect in plan.roof:
                element(floor, "object", type="roof", x=rect.x, y=rect.y,
                        width=rect.width, height=rect.height, RoofType="FlatTop", Depth="Three",
                        cappedW="false", cappedN="false", cappedE="false", cappedS="false",
                        CapTiles=ROOF_CAP, SlopeTiles=0, TopTiles=ROOF_TOP)
        element(floor, "rooms").text = _room_csv(plan_floor.grid, offset)
        offset += len(plan_floor.rooms)
    # BuildingEd places the depth-three roof surface on the next floor.
    roof_floor = element(root, "floor")
    element(roof_floor, "rooms").text = _room_csv(((0,) * plan.width,) * plan.height)
    return xml_document(root)
