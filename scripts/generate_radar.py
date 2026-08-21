# -*- coding: utf-8 -*-
"""Generate radar chart PNG via PIL for 6 modules 5 dims."""
from PIL import Image, ImageDraw, ImageFont
import math, os

# Scores per handover PM rubric: 功能,易用,清晰,完整,信任
data = {
    "A 采集": [4,3,3,3,3],
    "B 库存": [3,3,3,2,4],
    "C 供应商": [4,3,3,3,4],
    "D AI/Gap": [3,3,2,2,3],
    "E 治理": [4,3,3,3,4],
    "F 总审": [3,3,3,2,3],
}
labels = ["功能","易用","清晰","完整","信任"]
colors = {
    "A 采集": "#2563eb",
    "B 库存": "#dc2626",
    "C 供应商": "#059669",
    "D AI/Gap": "#d97706",
    "E 治理": "#7c3aed",
    "F 总审": "#db2777",
}
W,H = 800, 800
CX,CY = 400, 400
R = 280

img = Image.new("RGB", (W,H), "#ffffff")
draw = ImageDraw.Draw(img)

# grid circles 1-5
for r in [56,112,168,224,280]:
    pts=[]
    for i in range(5):
        ang = math.radians(-90 + i*72)
        pts.append((CX + r*math.cos(ang), CY + r*math.sin(ang)))
    draw.polygon(pts, outline="#e2e8f0", width=1)
    # labels at outer
for i,lab in enumerate(labels):
    ang = math.radians(-90 + i*72)
    x = CX + (R+28)*math.cos(ang)
    y = CY + (R+28)*math.sin(ang)
    draw.text((x-18,y-8), lab, fill="#111827")

# axes
for i in range(5):
    ang = math.radians(-90 + i*72)
    draw.line((CX,CY, CX+R*math.cos(ang), CY+R*math.sin(ang)), fill="#cbd5e1", width=1)

# polygons per module
for name, scores in data.items():
    pts=[]
    for i,s in enumerate(scores):
        ang = math.radians(-90 + i*72)
        r = s/5 * R
        pts.append((CX + r*math.cos(ang), CY + r*math.sin(ang)))
    col = colors[name]
    # translucent fill via polygon + outline
    draw.polygon(pts, fill=None, outline=col, width=2)
    for x,y in pts:
        draw.ellipse((x-4,y-4,x+4,y+4), fill=col, outline="#ffffff", width=1)

# legend
lx, ly = 30, 650
for idx,(name,scores) in enumerate(data.items()):
    col = colors[name]
    y = ly + idx*20
    draw.rectangle((lx, y, lx+14, y+14), fill=col)
    draw.text((lx+20, y), f"{name} {'/'.join(map(str,scores))} avg{sum(scores)/5:.1f}", fill="#111827")

# title
draw.text((W//2-180, 30), "E2E PM 5维雷达 (1-5, <4缺陷)", fill="#111827")
draw.text((W//2-260, 55), "真实收据 batch1 163张 + 合成 Gap 8 — Orca 真机 + API", fill="#64748b")

out = "artifacts/e2e/radar.png"
os.makedirs(os.path.dirname(out), exist_ok=True)
img.save(out, quality=95)
print(f"radar saved {out}")

# also copy to root for report
Image.open(out).save("artifacts/radar.png")
print("copied to artifacts/radar.png")
