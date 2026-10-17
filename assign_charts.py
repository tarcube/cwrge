#!/usr/bin/env python3
"""
CWRGE chart assignment.

Reads campus building + path GeoJSON exported from ArcGIS Pro, builds a graph
from the path network, measures each building's hop distance from its team's
home cluster, and assigns poll-submitted charts so difficulty rises with
distance from home.

Outputs a flat CSV.

Dependencies: networkx, pandas, numpy.
"""

import argparse
import json
import random
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import networkx as nx

def _coords(geom):
    """Flatten any GeoJSON geometry to a list of (x, y) vertices."""
    t = geom["type"]
    c = geom["coordinates"]
    if t == "Point":
        return [tuple(c[:2])]
    if t in ("MultiPoint", "LineString"):
        return [tuple(p[:2]) for p in c]
    if t in ("MultiLineString", "Polygon"):
        return [tuple(p[:2]) for ring in c for p in ring]
    if t == "MultiPolygon":
        return [tuple(p[:2]) for poly in c for ring in poly for p in ring]
    raise ValueError(f"unsupported geometry type: {t}")


def representative_point(geom):
    pts = np.array(_coords(geom), dtype=float)
    return tuple(pts.mean(axis=0))


def load_buildings(path, campus_field, num_field, name_field, centroids_path=None):
    with open(path) as f:
        gj = json.load(f)

    centroid_xy = None
    if centroids_path:
        with open(centroids_path) as f:
            cgj = json.load(f)
        centroid_xy = {}
        for feat in cgj["features"]:
            p = feat["properties"]
            key = (str(p[campus_field]).strip(), int(p[num_field]))
            centroid_xy[key] = representative_point(feat["geometry"])

    rows = []
    missing_centroid = 0
    for feat in gj["features"]:
        p = feat["properties"]
        campus = str(p[campus_field]).strip()
        num = int(p[num_field])
        if centroid_xy is not None:
            xy = centroid_xy.get((campus, num))
            if xy is None:
                missing_centroid += 1
                xy = representative_point(feat["geometry"])
            x, y = xy
        else:
            x, y = representative_point(feat["geometry"])
        rows.append({
            "campus": campus,
            "num": num,
            "name": str(p.get(name_field, "")).strip(),
            "x": x,
            "y": y,
        })
    if missing_centroid:
        print(f"  ! {missing_centroid} buildings had no matching Centre "
              f"Point, fell back to polygon centroid -- run "
              f"validate_geojson.py to find them", file=sys.stderr)
    df = pd.DataFrame(rows)
    dupes = df.duplicated(subset=["campus", "num"]).sum()
    if dupes:
        print(f"  ! {dupes} duplicate (campus, num) pairs -- check your data",
              file=sys.stderr)
    return df


def load_path_endpoints(path):
    """Return a list of ((x1,y1), (x2,y2)) for every path line."""
    with open(path) as f:
        gj = json.load(f)
    out = []
    for feat in gj["features"]:
        g = feat["geometry"]
        if g["type"] == "LineString":
            segs = [g["coordinates"]]
        elif g["type"] == "MultiLineString":
            segs = g["coordinates"]
        else:
            continue
        for seg in segs:
            if len(seg) >= 2:
                out.append((tuple(seg[0][:2]), tuple(seg[-1][:2])))
    return out


def snap(pt, xy, tol):
    """Index of nearest building centroid, or None if beyond tol."""
    d = np.hypot(xy[:, 0] - pt[0], xy[:, 1] - pt[1])
    i = int(d.argmin())
    return i if (tol is None or d[i] <= tol) else None

def build_graph(bdf, endpoints, tol):
    """One graph per campus, nodes keyed by building number."""
    graphs = {}
    unsnapped = 0
    for campus, grp in bdf.groupby("campus"):
        grp = grp.reset_index(drop=True)
        xy = grp[["x", "y"]].to_numpy()
        G = nx.Graph()
        G.add_nodes_from(grp["num"].tolist())
        graphs[campus] = (G, grp, xy)

    campuses = list(graphs)
    for a, b in endpoints:
        best = None
        for campus in campuses:
            _, grp, xy = graphs[campus]
            ia = snap(a, xy, tol)
            ib = snap(b, xy, tol)
            if ia is None or ib is None or ia == ib:
                continue
            da = np.hypot(xy[ia, 0] - a[0], xy[ia, 1] - a[1])
            db = np.hypot(xy[ib, 0] - b[0], xy[ib, 1] - b[1])
            if best is None or da + db < best[0]:
                best = (da + db, campus, grp.at[ia, "num"], grp.at[ib, "num"])
        if best is None:
            unsnapped += 1
        else:
            _, campus, na, nb = best
            graphs[campus][0].add_edge(int(na), int(nb))

    if unsnapped:
        print(f"  ! {unsnapped} path lines could not be snapped to two "
              f"buildings (raise --snap-tol?)", file=sys.stderr)
    return {c: g for c, (g, _, _) in graphs.items()}


def hop_depths(G, home_nodes, seed=None):
    """Multi-source BFS hop count. Unreachable nodes get random depth."""
    rng = random.Random(seed)
    sources = [n for n in home_nodes if n in G]
    if not sources:
        raise ValueError("no home nodes present in this campus graph")
    depth = {}
    for src in sources:
        for node, d in nx.single_source_shortest_path_length(G, src).items():
            if node not in depth or d < depth[node]:
                depth[node] = d
    far = max(depth.values()) if depth else 0
    orphans = [n for n in G.nodes if n not in depth]
    for n in orphans:
        depth[n] = rng.randint(0, max(far, 1))
    if orphans:
        print(f"  ! {len(orphans)} nodes unreachable from home; "
              f"assigned random depths (fix path network): "
              f"{sorted(orphans)[:10]}", file=sys.stderr)
    return depth


def bands_from_depth(depth, n_bands):
    """Quantile-bin hop depth so every band gets a workable share of nodes."""
    s = pd.Series(depth)
    if s.nunique() == 1:
        return {k: 1 for k in depth}
    ranks = s.rank(method="average", pct=True)
    band = np.ceil(ranks * n_bands).clip(1, n_bands).astype(int)
    return band.to_dict()

def assign(nodes, charts, n_bands, arcade_games, arcade_cap, submitter_cap,
           seed):
    """
    nodes: DataFrame with campus, num, band
    charts: DataFrame with song, chart, game, platform, tier, submitter
    Returns nodes with chart columns attached.
    """
    rng = random.Random(seed)

    pool = charts.to_dict("records")
    rng.shuffle(pool)
    by_tier = defaultdict(list)
    for c in pool:
        by_tier[int(c["tier"])].append(c)

    used_by_submitter = defaultdict(int)
    arcade_used = defaultdict(int)
    arcade_quota = {
        b: max(1, int(round(arcade_cap * (nodes["band"] == b).sum())))
        for b in range(1, n_bands + 1)
    }

    order = nodes.sort_values(["band", "campus", "num"]).to_dict("records")
    out, shortfall = [], 0

    for node in order:
        band = node["band"]

        tier_order = sorted(range(1, n_bands + 1), key=lambda t: (abs(t - band), t))
        pick = None
        for t in tier_order:
            for i, cand in enumerate(by_tier[t]):
                if used_by_submitter[cand["submitter"]] >= submitter_cap:
                    continue
                is_arcade = cand["game"] in arcade_games and \
                    str(cand["platform"]).lower() == "arcade"
                if is_arcade and arcade_used[band] >= arcade_quota[band]:
                    continue
                pick = by_tier[t].pop(i)
                if is_arcade:
                    arcade_used[band] += 1
                break
            if pick:
                break
        if pick is None:
            shortfall += 1
            row = dict(node, song="", chart="", game="", platform="",
                       tier="", submitter="")
        else:
            used_by_submitter[pick["submitter"]] += 1
            row = dict(node, **{k: pick[k] for k in
                                ("song", "chart", "game", "platform",
                                 "tier", "submitter")})
        out.append(row)

    if shortfall:
        print(f"  ! {shortfall} nodes left unassigned -- the chart pool is "
              f"too small or the caps are too tight", file=sys.stderr)
    return pd.DataFrame(out)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--buildings", required=True, help="buildings GeoJSON")
    ap.add_argument("--centroids", default=None,
                    help="Centre Points GeoJSON (Feature to Point output); "
                         "preferred node coordinate source when given")
    ap.add_argument("--paths", required=True, help="paths GeoJSON")
    ap.add_argument("--charts", required=True, help="charts CSV from the poll")
    ap.add_argument("--home", required=True,
                    help='JSON: {"Waterloo": [13, 39, 7], ...}')
    ap.add_argument("--out", default="chart_assignments.csv")
    ap.add_argument("--campus-field", default="Campus")
    ap.add_argument("--num-field", default="Num")
    ap.add_argument("--name-field", default="Name")
    ap.add_argument("--bands", type=int, default=5)
    ap.add_argument("--snap-tol", type=float, default=None,
                    help="max snap distance in layer units; omit for nearest")
    ap.add_argument("--arcade-games", default="IIDX,SDVX,maimai,Chunithm")
    ap.add_argument("--arcade-cap", type=float, default=0.25,
                    help="max share of each band that may be arcade-only")
    ap.add_argument("--submitter-cap", type=int, default=6,
                    help="max nodes any one person's submissions can fill")
    ap.add_argument("--seed", type=int, default=20261017)
    a = ap.parse_args()

    print("reading layers...")
    bdf = load_buildings(a.buildings, a.campus_field, a.num_field,
                         a.name_field, a.centroids)
    if a.centroids:
        print(f"  using Centre Points from {a.centroids} for node coordinates")
    endpoints = load_path_endpoints(a.paths)
    print(f"  {len(bdf)} buildings, {len(endpoints)} path lines")

    graphs = build_graph(bdf, endpoints, a.snap_tol)

    with open(a.home) as f:
        home = json.load(f)

    rows = []
    for campus, G in graphs.items():
        if campus not in home:
            print(f"  ! no home cluster defined for {campus}, skipping",
                  file=sys.stderr)
            continue
        depth = hop_depths(G, [int(n) for n in home[campus]], seed=a.seed)
        band = bands_from_depth(depth, a.bands)
        deg = dict(G.degree())
        for num in sorted(depth):
            rows.append({"campus": campus, "num": num,
                         "hops": depth[num], "band": band[num],
                         "degree": deg.get(num, 0)})
    nodes = pd.DataFrame(rows)
    nodes = nodes.merge(bdf[["campus", "num", "name"]],
                        on=["campus", "num"], how="left")

    charts = pd.read_csv(a.charts)
    need = {"song", "chart", "game", "platform", "tier", "submitter"}
    missing = need - set(charts.columns)
    if missing:
        sys.exit(f"charts CSV is missing columns: {sorted(missing)}")
    print(f"  {len(charts)} charts in pool for {len(nodes)} nodes")

    result = assign(nodes, charts, a.bands,
                    set(g.strip() for g in a.arcade_games.split(",")),
                    a.arcade_cap, a.submitter_cap, a.seed)

    cols = ["campus", "num", "name", "hops", "band", "degree",
            "song", "chart", "game", "platform", "tier", "submitter"]
    result[cols].to_csv(a.out, index=False)
    print(f"wrote {a.out}")

    print("\nband / hop QA:")
    qa = result.groupby("band").agg(
        nodes=("num", "size"),
        min_hops=("hops", "min"),
        max_hops=("hops", "max"),
        mean_tier=("tier", lambda s: pd.to_numeric(s, errors="coerce").mean()),
    )
    print(qa.to_string())


if __name__ == "__main__":
    main()
