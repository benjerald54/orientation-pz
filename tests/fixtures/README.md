# PZ structural fixture

`pz_golden.json` contains independently hand-authored geometry and expected
semantic structures for rectangle, L-shape, rotated, multistorey, and
cell-boundary cases. Dimensions, occupied cells, masks, stair cores and roof
rectangles are the oracle; XML formatting is not. The local tile Y axis is the
reverse of the metric fixture's Y axis. These fixtures are not editor-loaded
known-good projects; successful comparisons do not verify WorldEd compatibility.

## KnoxMap project-format reference

`knoxmap_world.pzw` was emitted by unmodified KnoxMap `knoxbuild.world.render_pzw`
at `79ebdc25b0321e188a29d5d43ed7b22e73063bbe`. Inputs: 2×2 cells,
`bmp_name="terrain & base.bmp"`, one `Placement("buildings/a&b.tbx", 299, 301, 10, 8)`,
`project_dir="/PROJECT"`, default empty map name and zones, default origin `(70, 0)`.
`/PROJECT` is a synthetic path, with no existing TMX files. This is actual writer
output, not copied implementation code, a complete terrain pipeline run, or an
editor-loaded project. No terrain/TBX assets accompany this format-only fixture.
Tests normalize export destination paths and cell enumeration order only.
The writer's unused zone declarations are outside the compared subset because
our exporter emits no zone objects. Its conversion settings, origins, bitmap,
and all cell/lot attributes are compared without changing expected values.

## Building model and placements

`pz_multistorey.json` is a hand-authored input and structural oracle, not a
snapshot regenerated from the serializer. Rectangles and LocalGrid domains
are in each grid's local metric axes with one meter per tile. Grid 9 is rotated
30 degrees in projected space. The test converts them with the compiler's
ProjectionContext before generation. Its domain uses quarter-tile bounds;
integer snapping places the frame at (-50, 350) without floating-point ties.

It covers a two-storey 8×12 building crossing the 300-tile cell boundary, a
three-storey 12×4 building requiring west-facing stairs, and a four-storey
3×6 building in a separate LocalGrid. Expected coordinates follow
`tile_x = local_metric_x - snapped_min_x`,
`tile_y = snapped_max_y - local_metric_max_y`, and 300-tile cell division/modulo.
Here `snapped_min_x = floor(domain_min_x)` and
`snapped_max_y = ceil(domain_max_y)` are the one-meter tile-frame origins
computed by `GridTileFrame.from_local_grid`, after rotating into local axes.
No timestamps, machine paths, or floating-point georeferencing are snapshotted.

The structural contract is based on KnoxMap's Plan/Building separation and
PZ Mapping Tools' readers and stair geometry (revisions in
`docs/pz-generation.md`). In particular, a stair's five-cell footprint includes
two landings and three step cells; N runs in positive Y and W in positive X.
BuildingEd removes only the three step cells from the floor above. The room
grid remains occupied there. The highest occupied floor has no outgoing stairs;
the additional empty floor receives the flat roof surface.

The expectations also fix our deterministic core/entrance/window choices;
they do not claim to be a game-rendered fixture or copied KnoxMap output.
