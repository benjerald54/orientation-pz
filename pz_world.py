"""WorldEd lot placement. TBX coordinates remain local to each building."""

from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

from pz_tbx import element, xml_document


CELL_SIZE = 300
DEFAULT_WORLD_ORIGIN = (70, 0)
PROJECT_GAP_CELLS = 2


def validate_world_origin(world_origin):
    if len(world_origin) != 2 or any(type(v) is not int for v in world_origin):
        raise ValueError("world origin must contain two integer cell coordinates")


def bind_project_paths(document, project_directory):
    """Bind export destinations on write, using KnoxMap's native path convention.

    Resource references (TBX/BMP) remain relative to the PZW when supplied that
    way. Only existing terrain TMX files are attached; no map is fabricated.
    """
    root = ET.fromstring(document)
    base = Path(project_directory).resolve()
    for selector in ("BMPToTMX/tmxexportdir", "GenerateLots/exportdir"):
        node = root.find(selector)
        node.set("path", (base / node.get("path")).resolve().as_posix())
    bmp = root.find("bmp")
    if bmp is not None:
        stem = PurePosixPath(bmp.get("path")).stem
        ox, oy = map(int, root.find("GenerateLots/worldOrigin").get("origin").split(","))
        for cell in root.findall("cell"):
            tmx = base / "tmx" / f"{stem}_{ox + int(cell.get('x'))}_{oy + int(cell.get('y'))}.tmx"
            if tmx.is_file():
                cell.set("map", tmx.as_posix())
    return xml_document(root)


@dataclass(frozen=True)
class LotPlacement:
    tbx_path: str
    x: int
    y: int
    width: int
    height: int

    def to_dict(self):
        return {**asdict(self), "cell": [self.x // CELL_SIZE, self.y // CELL_SIZE],
                "offset": [self.x % CELL_SIZE, self.y % CELL_SIZE], "level": 0}


def render_pzw(placements, width_tiles, height_tiles, world_origin=DEFAULT_WORLD_ORIGIN,
               *, terrain_bmp=""):
    if any(type(v) is not int or v <= 0 for v in (width_tiles, height_tiles)):
        raise ValueError("world dimensions must be positive integer tile counts")
    validate_world_origin(world_origin)
    columns = (width_tiles + CELL_SIZE - 1) // CELL_SIZE
    rows = (height_tiles + CELL_SIZE - 1) // CELL_SIZE
    by_cell = defaultdict(list)
    for lot in placements:
        if (any(type(v) is not int for v in (lot.x, lot.y, lot.width, lot.height))
                or lot.x < 0 or lot.y < 0 or not 1 <= lot.width <= 300
                or not 1 <= lot.height <= 300
                or lot.x + lot.width > width_tiles or lot.y + lot.height > height_tiles):
            raise ValueError("lot outside project bounds")
        path = PurePosixPath(lot.tbx_path)
        if (path.is_absolute() or ".." in path.parts or "\\" in lot.tbx_path
                or ":" in lot.tbx_path or path.suffix != ".tbx"):
            raise ValueError("lot must reference a relative TBX path")
        by_cell[lot.x // CELL_SIZE, lot.y // CELL_SIZE].append(lot)
    root = ET.Element("world", version="1.0", width=str(columns), height=str(rows))
    conversion = element(root, "BMPToTMX")
    element(conversion, "tmxexportdir", path="tmx")
    for name in ("rulesfile", "blendsfile", "mapbasefile"):
        element(conversion, name, path="")
    for name in ("assign-maps-to-world", "warn-unknown-colors", "compress", "copy-pixels"):
        element(conversion, name, checked="true")
    element(conversion, "update-existing", checked="false")
    reverse = element(root, "TMXToBMP")
    element(reverse, "mainImage", generate="true")
    element(reverse, "vegetationImage", generate="true")
    element(reverse, "buildingsImage", path="", generate="false")
    settings = element(root, "GenerateLots")
    element(settings, "exportdir", path="lots")
    element(settings, "ZombieSpawnMap", path="")
    element(settings, "TileDefFolder", path="")
    element(settings, "worldOrigin", origin=f"{world_origin[0]},{world_origin[1]}")
    element(settings, "numberOfThreads", count=4)
    lua = element(root, "LuaSettings")
    element(lua, "spawnPointsFile", path="spawnpoints.lua")
    element(lua, "worldObjectsFile", path="objects.lua")
    if terrain_bmp:
        element(root, "bmp", path=str(terrain_bmp), x=0, y=0, width=columns, height=rows)
    for cy in range(rows):
        for cx in range(columns):
            cell = element(root, "cell", x=cx, y=cy, map="")
            for lot in sorted(by_cell[cx, cy], key=lambda p: (p.y, p.x, p.tbx_path)):
                element(cell, "lot", x=lot.x % CELL_SIZE, y=lot.y % CELL_SIZE, level=0,
                        width=lot.width, height=lot.height, map=lot.tbx_path)
    return xml_document(root)
