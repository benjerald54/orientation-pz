"""Deterministic building plans in local tiles, independent of XML and placement."""

from collections import deque
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Opening:
    kind: str
    x: int
    y: int
    direction: str  # N = horizontal edge, W = vertical edge


@dataclass(frozen=True)
class Furniture:
    x: int
    y: int
    tile: str
    orientation: str = "N"


@dataclass(frozen=True)
class RoofRectangle:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Room:
    name: str = "livingroom"


@dataclass(frozen=True)
class Stair:
    x: int
    y: int
    direction: str

    @property
    def cells(self):
        return tuple((self.x, self.y + i) if self.direction == "N" else (self.x + i, self.y)
                     for i in range(5))

    @property
    def steps(self):
        # The endpoints are landings; BuildingEd cuts only these three tiles
        # out of the floor above and supplies their stair tiles from below.
        return self.cells[1:4]


@dataclass(frozen=True)
class StairCore:
    x: int
    y: int
    width: int
    height: int

    @property
    def cells(self):
        return {(x, y) for y in range(self.y, self.y + self.height)
                for x in range(self.x, self.x + self.width)}

    @property
    def stair(self):
        return Stair(self.x + 1, self.y + 1, "N" if self.height == 6 else "W")


@dataclass(frozen=True)
class Plan:
    width: int
    height: int
    rooms: tuple[Room, ...]
    grid: tuple[tuple[int, ...], ...]  # floor-local 1-based room IDs, 0 outside
    openings: tuple[Opening, ...] = ()
    furniture: tuple[Furniture, ...] = ()
    stairs: tuple[Stair, ...] = ()


@dataclass(frozen=True)
class BuildingPlan:
    width: int
    height: int
    floors: tuple[Plan, ...]
    roof: tuple[RoofRectangle, ...]
    core: StairCore | None = None

    def to_dict(self):
        return asdict(self)


def _neighbors(x, y):
    return ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))


def _flood(start, allowed):
    seen = {start}
    pending = deque([start])
    while pending:
        for neighbor in _neighbors(*pending.popleft()):
            if neighbor not in seen and allowed(*neighbor):
                seen.add(neighbor)
                pending.append(neighbor)
    return seen


def exterior_edges(mask):
    """(opening x, y, N/W, facade, inside tile) adjoining reachable outside.

    Flood the padded background so a courtyard is not an exterior entrance.
    South and east facades use the N/W edge of the neighboring tile.
    """
    height, width = len(mask), len(mask[0])
    outside = _flood((-1, -1), lambda x, y:
                     -1 <= x <= width and -1 <= y <= height
                     and not (0 <= x < width and 0 <= y < height and mask[y][x]))
    edges = []
    for y, row in enumerate(mask):
        for x, occupied in enumerate(row):
            if not occupied:
                continue
            for nx, ny, ex, ey, direction, side in (
                (x, y - 1, x, y, "N", "north"),
                (x - 1, y, x, y, "W", "west"),
                (x, y + 1, x, y + 1, "N", "south"),
                (x + 1, y, x + 1, y, "W", "east"),
            ):
                if (nx, ny) in outside:
                    edges.append((ex, ey, direction, side, (x, y)))
    return edges


def roof_rectangles(mask):
    """Cover occupied cells exactly once; extend equal horizontal runs downward."""
    active = {}
    finished = []
    for y, row in enumerate((*mask, (0,) * len(mask[0]))):
        runs = []
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            start = x
            while x < len(row) and row[x]:
                x += 1
            runs.append((start, x - start))
        for run in sorted(active.keys() - set(runs)):
            start_y = active.pop(run)
            finished.append(RoofRectangle(run[0], start_y, run[1], y - start_y))
        for run in runs:
            active.setdefault(run, y)
    return tuple(sorted(finished, key=lambda r: (r.y, r.x, r.height, r.width)))


def reserve_stair_core(mask, entrance_side=None):
    """Reserve a full flight, its landings, and side circulation before layout."""
    h, w = len(mask), len(mask[0])
    best = None
    best_rank = None
    for cw, ch in ((3, 6), (6, 3)):
        for y in range(h - ch + 1):
            for x in range(w - cw + 1):
                if not all(mask[cy][cx] for cy in range(y, y + ch) for cx in range(x, x + cw)):
                    continue
                # Prefer the back of the building, then the center; stable ties.
                back = {"south": y, "north": h - y - ch,
                        "east": x, "west": w - x - cw}.get(entrance_side, 0)
                center = abs(2 * x + cw - w) + abs(2 * y + ch - h)
                rank = (back, center, y, x, cw)
                if best_rank is None or rank < best_rank:
                    best_rank, best = rank, StairCore(x, y, cw, ch)
    if best is None:
        raise ValueError("no valid stair core for requested building levels")
    return best


def build_plan(footprint, levels=1, entrance_side=None, windows=True, furnish=False):
    """One room per storey. Reject disconnected masks instead of discarding wings.

    Reserve aligned stairs and circulation before room/furniture generation.
    All variation uses fixed ordering, never hash/random.
    """
    if type(levels) is not int or not 1 <= levels <= 30:
        raise ValueError("building levels must be an integer from 1 to 30")
    w, h, mask = footprint.width, footprint.height, footprint.mask
    if not (1 <= w <= 300 and 1 <= h <= 300):
        raise ValueError("building dimensions must be from 1 to 300 tiles")
    if len(mask) != h or any(len(row) != w for row in mask):
        raise ValueError("mask dimensions do not match footprint")
    if any(value not in (0, 1) for row in mask for value in row):
        raise ValueError("mask must contain only 0 and 1")
    occupied = {(x, y) for y, row in enumerate(mask) for x, value in enumerate(row) if value}
    if not occupied:
        raise ValueError("empty tile mask")
    connected = _flood(min(occupied), lambda x, y: (x, y) in occupied)
    if connected != occupied:
        raise ValueError("disconnected tile mask requires separate buildings")
    core = reserve_stair_core(mask, entrance_side) if levels > 1 else None
    reserved = core.cells if core else set()
    flight = set(core.stair.cells) if core else set()
    steps = set(core.stair.steps) if core else set()
    edges = [edge for edge in exterior_edges(mask) if edge[4] not in flight]
    if not edges:
        raise ValueError("no exterior entrance edge remains outside the stair flight")

    def rank(edge):
        x, y, direction, side, _ = edge
        # Prefer supplied frontage, then the middle of the bounding facade.
        along = x + 0.5 - w / 2 if direction == "N" else y + 0.5 - h / 2
        return (side != (entrance_side or "south"), abs(along), y, x, direction)

    entrance = min(edges, key=rank)
    door = Opening("door", *entrance[:3])
    window_edges = [edge for edge in edges if edge[4] != entrance[4] and edge[4] not in reserved]
    window = min(window_edges, key=lambda edge:
                 (edge[3] == entrance[3], *rank(edge))) if window_edges else None
    furnishings = ()
    if furnish:
        # Keep the entry tile and its neighbors clear for walking in.
        candidates = sorted(occupied - reserved - {entrance[4], *_neighbors(*entrance[4])},
                            key=lambda p: (p[1], p[0]))
        if candidates:
            x, y = candidates[-1]
            walkable = occupied - steps - {(x, y)}
            if _flood(entrance[4], lambda cx, cy: (cx, cy) in walkable) == walkable:
                furnishings = (Furniture(x, y, "furniture_seating_indoor_01_037"),)
    grid = tuple(tuple(int(value) for value in row) for row in mask)
    floors = []
    for level in range(levels):
        openings = (door,) if level == 0 else ()
        if windows and window:
            openings += (Opening("window", *window[:3]),)
        stairs = (core.stair,) if core and level < levels - 1 else ()
        floors.append(Plan(w, h, (Room(),), grid, openings, furnishings, stairs))
    return BuildingPlan(w, h, tuple(floors), roof_rectangles(mask), core)
