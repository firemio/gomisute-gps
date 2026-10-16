import geopandas as gpd, matplotlib, numpy as np
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.geometry import Point
z = gpd.read_file("out/kanazawa_zones.geojson").to_crs(6675)
parts = gpd.read_file("out/kanazawa_town_parts.geojson").to_crs(6675)
rng = np.random.default_rng(7)
z["c"] = [tuple(rng.uniform(0.25, 0.95, 3)) for _ in range(len(z))]
c = gpd.GeoSeries([Point(136.6485, 36.5613)], crs=4326).to_crs(6675).iloc[0]   # 香林坊付近
box = (c.x - 2500, c.y - 2500, c.x + 2500, c.y + 2500)
fig, ax = plt.subplots(1, 2, figsize=(18, 9))
z.plot(ax=ax[0], color=z["c"], edgecolor="white", linewidth=0.3)
ax[0].add_patch(plt.Rectangle((box[0], box[1]), 5000, 5000, fill=False, color="red", lw=1.5))
ax[0].set_title("Kanazawa: 72 collection districts"); ax[0].set_axis_off()
z.plot(ax=ax[1], color=z["c"], edgecolor="white", linewidth=0.8)
parts[parts["method"] == "voronoi"].boundary.plot(ax=ax[1], color="black", linewidth=0.4)
for _, r in z.cx[box[0]:box[2], box[1]:box[3]].iterrows():
    p = r.geometry.intersection(__import__("shapely").box(*box)).representative_point()
    if not p.is_empty: ax[1].annotate(r["district"], (p.x, p.y), fontsize=7, ha="center", fontfamily="MS Gothic")
ax[1].set_xlim(box[0], box[2]); ax[1].set_ylim(box[1], box[3])
ax[1].set_title("downtown 5km - black = towns split by 街区/番地 (Voronoi)"); ax[1].set_axis_off()
plt.tight_layout(); plt.savefig("out/kanazawa_map.png", dpi=90); print("ok")
