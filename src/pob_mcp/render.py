"""Draws the passive tree as a PNG with Pillow."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

BG = (14, 14, 18)
EDGE = (55, 55, 62)
EDGE_ALLOC = (196, 158, 74)
EDGE_HL = (60, 200, 230)
NODE = (95, 95, 105)
NODE_ALLOC = (232, 190, 92)
NODE_HL = (80, 220, 250)
KEYSTONE = (170, 120, 210)
SOCKET = (90, 160, 110)
TEXT = (230, 230, 230)

SIZES = {"n": 2, "N": 4, "K": 6, "S": 4, "M": 2, "C": 6}
MASTERY = (45, 45, 52)


def render_tree(data: dict, width: int = 1600, crop: bool = True, margin: int = 1500) -> bytes:
    nodes = {n[0]: n for n in data["nodes"]}  # id -> [id, x, y, type, alloc, highlight]
    if not nodes:
        raise ValueError("No tree nodes to draw")

    # Bounding box: whole tree, or the allocated/highlighted area plus a margin
    focus = [n for n in nodes.values() if n[4] or n[5]] if crop else []
    pts = focus if focus else list(nodes.values())
    min_x = min(n[1] for n in pts) - (margin if focus else 200)
    max_x = max(n[1] for n in pts) + (margin if focus else 200)
    min_y = min(n[2] for n in pts) - (margin if focus else 200)
    max_y = max(n[2] for n in pts) + (margin if focus else 200)
    scale = width / max(1, (max_x - min_x))
    height = max(200, int((max_y - min_y) * scale))
    if height > width * 1.5:  # keep tall crops readable
        scale = (width * 1.5) / (max_y - min_y)
        height = int(width * 1.5)

    def xy(n):
        return ((n[1] - min_x) * scale, (n[2] - min_y) * scale)

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    line_w = max(1, int(round(scale * 12)))

    for a, b in data["edges"]:
        na, nb = nodes.get(a), nodes.get(b)
        if not na or not nb:
            continue
        if na[5] and (nb[5] or nb[4]) or nb[5] and (na[5] or na[4]):
            color, w = EDGE_HL, line_w + 2
        elif na[4] and nb[4]:
            color, w = EDGE_ALLOC, line_w + 1
        else:
            color, w = EDGE, line_w
        d.line([xy(na), xy(nb)], fill=color, width=w)

    node_scale = max(0.6, min(3.0, scale * 60))
    for n in nodes.values():
        x, y = xy(n)
        r = SIZES.get(n[3], 2) * node_scale
        if n[5]:
            fill = NODE_HL
        elif n[4]:
            fill = NODE_ALLOC
        elif n[3] == "K":
            fill = KEYSTONE
        elif n[3] == "S":
            fill = SOCKET
        elif n[3] == "M":
            fill = MASTERY
        else:
            fill = NODE
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    try:
        font = ImageFont.load_default(size=max(10, int(11 * min(2.0, node_scale))))
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    for nid, name in data.get("labels", []):
        n = nodes.get(nid)
        if not n or not (n[4] or n[5] or n[3] == "K"):
            continue
        x, y = xy(n)
        r = SIZES.get(n[3], 2) * node_scale
        if 0 <= x <= width and 0 <= y <= height:
            d.text((x + r + 3, y - 7), name, fill=TEXT if (n[4] or n[5]) else KEYSTONE, font=font)

    title = f"{data.get('className') or ''} {data.get('ascendancy') or ''}".strip()
    d.text((10, 8), title + "   gold = allocated, cyan = highlighted route", fill=TEXT, font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
