#!/usr/bin/env python3
"""
CWRGE export validator.

Run this on buildings.geojson, paths.geojson, and centroids.geojson right
after exporting from ArcGIS Pro.

Usage:
    python validate_geojson.py --buildings buildings.geojson \
        --paths paths.geojson --centroids centroids.geojson
"""

import argparse
import json
import sys
from collections import Counter

import numpy as np

EXPECTED_COUNTS = {
    "British Columbia": 76,
    "Alberta": 60,
    "Waterloo": 58,
    "Ontario Tech": 32,
    "Western": 54,
    "McMaster": 56,
    "Toronto": 99,
}


CANADA_LAT = (41.5, 60.0)
CANADA_LON = (-141.0, -52.0)


def load(path):
    with open(path) as f:
        return json.load(f)


def flat_xy(geom):
    t, c = geom["type"], geom["coordinates"]
    if t == "Point":
        return [tuple(c[:2])]
    if t in ("MultiPoint", "LineString"):
        return [tuple(p[:2]) for p in c]
    if t in ("MultiLineString", "Polygon"):
        return [tuple(p[:2]) for ring in c for p in ring]
    if t == "MultiPolygon":
        return [tuple(p[:2]) for poly in c for ring in poly for p in ring]
    raise ValueError(f"unsupported geometry: {t}")


def check_fields(gj, required, label):
    print(f"\n--- {label}: field check ---")
    if not gj["features"]:
        print("  ! file has no features")
        return False
    props = gj["features"][0]["properties"]
    missing = [f for f in required if f not in props]
    if missing:
        print(f"  ! missing fields: {missing}")
        print(f"    fields present: {list(props.keys())}")
        return False
    print(f"  ok -- found {required}")
    return True


def check_coords(gj, label):
    print(f"\n--- {label}: coordinate sanity ---")
    bad = []
    for feat in gj["features"]:
        for x, y in flat_xy(feat["geometry"]):
            if not (CANADA_LON[0] <= x <= CANADA_LON[1]):
                bad.append((feat["properties"], x, y, "longitude"))
            if not (CANADA_LAT[0] <= y <= CANADA_LAT[1]):
                bad.append((feat["properties"], x, y, "latitude"))
    if bad:
        print(f"  ! {len(bad)} vertices outside expected Canada bbox "
              f"-- likely not reprojected to WGS84")
        for props, x, y, axis in bad[:5]:
            print(f"    {props.get('Campus', '?')} #{props.get('Num', '?')}: "
                  f"({x:.4f}, {y:.4f}) -- bad {axis}")
        return False
    print(f"  ok -- all {len(gj['features'])} features within WGS84 Canada bbox")
    return True


def check_counts(gj, label):
    print(f"\n--- {label}: per-campus counts ---")
    counts = Counter(f["properties"].get("Campus", "?") for f in gj["features"])
    ok = True
    for campus, expected in EXPECTED_COUNTS.items():
        actual = counts.get(campus, 0)
        flag = "ok" if actual == expected else "!"
        if actual != expected:
            ok = False
        print(f"  {flag}  {campus:32s} expected {expected:3d}  got {actual:3d}")
    unexpected = set(counts) - set(EXPECTED_COUNTS)
    if unexpected:
        print(f"  ! campus names not in expected list: {sorted(unexpected)}")
        ok = False
    print(f"  total: {sum(counts.values())}")
    return ok


def check_duplicate_ids(gj, label):
    print(f"\n--- {label}: duplicate (Campus, Num) check ---")
    keys = [(f["properties"].get("Campus"), f["properties"].get("Num"))
            for f in gj["features"]]
    dupes = [k for k, n in Counter(keys).items() if n > 1]
    if dupes:
        print(f"  ! {len(dupes)} duplicate IDs: {dupes[:10]}")
        return False
    print(f"  ok -- all {len(keys)} IDs unique")
    return True


def check_path_connectivity(paths_gj, centroids_gj, tol):
    print(f"\n--- path-to-centroid connectivity ---")
    cpts = []
    for f in centroids_gj["features"]:
        x, y = flat_xy(f["geometry"])[0]
        cpts.append((x, y, f["properties"].get("Campus"),
                     f["properties"].get("Num")))
    xy = np.array([(p[0], p[1]) for p in cpts])

    def nearest(pt):
        d = np.hypot(xy[:, 0] - pt[0], xy[:, 1] - pt[1])
        i = int(d.argmin())
        return d[i], cpts[i]

    orphan_ends = 0
    checked = 0
    for feat in paths_gj["features"]:
        g = feat["geometry"]
        segs = [g["coordinates"]] if g["type"] == "LineString" else g.get("coordinates", [])
        for seg in segs:
            if len(seg) < 2:
                continue
            for end in (seg[0], seg[-1]):
                checked += 1
                d, nearest_pt = nearest(tuple(end[:2]))
                if d > tol:
                    orphan_ends += 1

    if orphan_ends:
        print(f"  ! {orphan_ends} / {checked} path endpoints have no Centre "
              f"Point within tolerance ({tol} deg ~= {tol*111000:.0f} m)")
        print(f"    raise --snap-tol if these are legitimate, or check for "
              f"paths drawn to the wrong layer")
        return False
    print(f"  ok -- all {checked} path endpoints snap to a Centre Point")
    return True


def check_building_centroid_match(buildings_gj, centroids_gj):
    """Centre Points should fall inside (or very near) their building."""
    print(f"\n--- buildings vs centroids: same (Campus, Num) set ---")
    b_keys = {(f["properties"].get("Campus"), f["properties"].get("Num"))
              for f in buildings_gj["features"]}
    c_keys = {(f["properties"].get("Campus"), f["properties"].get("Num"))
              for f in centroids_gj["features"]}
    only_b = b_keys - c_keys
    only_c = c_keys - b_keys
    ok = True
    if only_b:
        print(f"  ! {len(only_b)} buildings have no matching Centre Point: "
              f"{sorted(only_b)[:10]}")
        ok = False
    if only_c:
        print(f"  ! {len(only_c)} Centre Points have no matching building: "
              f"{sorted(only_c)[:10]}")
        ok = False
    if ok:
        print(f"  ok -- {len(b_keys)} buildings and centroids match 1:1")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--buildings", required=True)
    ap.add_argument("--paths", required=True)
    ap.add_argument("--centroids", required=True)
    ap.add_argument("--campus-field", default="Campus")
    ap.add_argument("--num-field", default="Num")
    ap.add_argument("--snap-tol", type=float, default=0.0005,
                    help="degrees; default ~55m, loosen if Feature-to-Point "
                         "placement differs from where paths were drawn")
    a = ap.parse_args()

    buildings = load(a.buildings)
    paths = load(a.paths)
    centroids = load(a.centroids)

    results = []
    required = [a.campus_field, a.num_field]

    results.append(check_fields(buildings, required + ["Name"], "buildings.geojson"))
    results.append(check_fields(centroids, required, "centroids.geojson"))

    results.append(check_coords(buildings, "buildings.geojson"))
    results.append(check_coords(centroids, "centroids.geojson"))

    results.append(check_counts(buildings, "buildings.geojson"))
    results.append(check_counts(centroids, "centroids.geojson"))

    results.append(check_duplicate_ids(buildings, "buildings.geojson"))
    results.append(check_duplicate_ids(centroids, "centroids.geojson"))

    results.append(check_building_centroid_match(buildings, centroids))
    results.append(check_path_connectivity(paths, centroids, a.snap_tol))

    print("\n" + "=" * 60)
    if all(results):
        print("ALL CHECKS PASSED -- safe to proceed to assign_charts.py / Leaflet")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} CHECK(S) FAILED -- fix these before building further")
        sys.exit(1)


if __name__ == "__main__":
    main()
