# orientation-pz

Compile a GeoJSON `FeatureCollection` by detecting local street-grid domains,
annotating feature orientations, and orthogonalizing geometry that follows
each domain's grid. Near-square or otherwise balanced features are marked
`undetermined` rather than assigned a cardinal orientation.

Install the geometry-validation dependency with `python -m pip install -r requirements.txt`.

Coordinate conversions share a `ProjectionContext` anchored at the map's mean
latitude. It currently uses a local equirectangular approximation and provides
one boundary for moving to a projected CRS later.

Domains are detected with a spatial-angular feature graph and density-based
core components. Grid bearings are fitted from each component, and the support
polygon is constructed from its member geometries rather than feature centers.
Defaults are a 250-meter search radius, at least three aligned features per
domain, and separate 15-degree tolerances for domain-angle agreement and edge
rectification. An optional property can prevent features with different values
from joining the same domain:

```sh
python orientation_detector.py converted-map.geojson oriented-map.geojson \
  --domain-radius-meters 250 --minimum-domain-features 3 \
  --domain-angle-tolerance-degrees 15 \
  --rectification-angle-tolerance-degrees 15 \
  --topology-tolerance-meters 0.05 \
  --building-road-adjacency-distance-meters 40 \
  --minimum-block-area-meters2 100 \
  --domain-property neighborhood_id
```

Omit the output path to write the result to standard output. Each feature gets
`dominant_orientation` (`horizontal`, `vertical`, or `undetermined`),
`dominant_orientation_degrees` (0, 90, or null), and
`orientation_confidence`, `local_grid_id`, `grid_bearing_degrees`,
`grid_confidence`, and `grid_evidence`. The top-level `local_grids` array
contains each grid's polygonal support domain, bearing, confidence, and fit
evidence. Segments within the rectification-angle tolerance of either local
grid axis are made exactly parallel to that axis; more diagonal segments remain
unchanged. The domain-angle tolerance is used only to discover compatible
features and does not control snapping.
Features outside an unambiguous support domain are not modified. Rectification
first builds a shared topology graph, inserts source vertices that fall on
another member's edge within the topology tolerance, and nodes proper segment
intersections. It then solves shared coordinate constraints across the graph.
This preserves repeated vertices and shared boundaries even when an input edge
has different vertex segmentation. Node identity is clustered in projected
meters using the topology tolerance rather than rounded longitude/latitude.
Rectification is semantic-aware: roads and railways use the configured
tolerance; building snapping is limited to closed polygon footprints with at
least 75% of perimeter near either grid axis and edges along both axes, and is
capped at 5 degrees. Water/natural features are not snapped, and unassigned
buildings stay unchanged. Each feature reports
`geometry_orthogonalized`, `orthogonalized_segments`, and
`topology_nodes_inserted`; the top-level `orthogonalization_summary` reports
detected domains, snapped segments, and inserted nodes. `--snap-tolerance`
remains as an alias for `--rectification-angle-tolerance-degrees`.

Before output, a geometry-validation gate checks zero-length edges, ring
closure, self-intersections, collapsed area, and hole preservation, then checks
that source inter-feature connections survive rectification. Invalid
rectifications are rolled back to source geometry; invalid source features are
marked `generation_eligible: false`. Procedural generation should consume only
`generation_ready_feature_indices`; `geometry_validation_summary` reports the
gate outcome. The top-level `validation_report` is deterministic: `features`
is ordered by zero-based `source_feature_index`, each record has `valid`,
`generation_ready`, sorted `reasons`, `repaired`, and `issue_details`; its
`grid_rectifications` records are ordered by `grid_id` and include
`rectification`, sorted `affected_features`, failure indices, and reason codes.
Grid rollback uses the reason `validation_failure` while preserving specific
feature-level causes such as `self_intersection`, `collapsed_polygon`, or
`topology_inconsistency`.

The top-level `road_network` contains normalized `highway`/`railway`
centerlines as `nodes` and `edges`. Crossings and T-junctions are noded in the
shared metric projection; node tolerance joins only nearby endpoints, while
parallel roads remain separate. `bridge`, `tunnel`, and `layer` tags define
grade separation, so disconnected crossings remain distinct components. Edges
retain source feature indices and properties for downstream attribution.
Each generation-ready building also gets a `road_relationship` record, collected
in the top-level `building_road_relationships` array. It reports the nearest
road and edge, closest building/road points, metric distance, approach bearing
and local-grid side, plus unique adjacent source roads within the configurable
40-meter default. Buildings between roads can therefore report both facades;
buildings with no available road have a null nearest-road record.

The `city_blocks` array is polygonized from closed, at-grade road-network faces;
buildings do not define block boundaries. Each `CityBlock` carries its polygonal
boundary, member road edge/node IDs, contained generation-ready building
indices, a majority local-grid summary, and area/perimeter/road-class metadata.
Bridge, tunnel, and non-zero-layer roads do not close an at-grade block.
`--minimum-block-area-meters2` filters tiny faces, while
`--road-boundary-tolerance-meters` controls road-edge attribution to a face.
Polygon edge lengths are capped at the median length for their snapped axis
when estimating orientation, reducing the influence of unusually long edges
such as sharp protrusions. Point-only features have an `undetermined`
orientation.

Run the tests with:

```sh
python -m unittest
```