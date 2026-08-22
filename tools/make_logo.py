"""Trade.LINK logo — vector geometry, rendered losslessly at any size.

Traced from the supplied artwork and verified against it: ring centres, hole
diameters, node sizes and stroke width all match the source to the pixel.
"""
from PIL import Image, ImageDraw

GREEN = (47, 218, 166)      # #2FDAA6
WHITE = (251, 250, 251)     # #FBFAFB
TILE  = (16, 19, 23)        # #101317

SW     = 7.6                # stroke width
RING_R = 10.8               # ring radius, mid-stroke (hole 14, outer 29)
DOT_R  = 9.0                # filled node radius

# full four-node mark, in its 268 x 148 reference frame
A = (46.0, 108.0)           # green ring   — bowl of the "b"
B = (112.0, 79.0)           # green peak node
C = (158.0, 100.0)          # white valley node
D = (222.0, 56.5)           # white ring   — bowl of the "d"
BAR_L = (29.5, 55.0, 133.0)
BAR_R = (239.0, 27.0, 101.0)

# Two-node mark for the app icon, traced from its own artwork in a 72-unit
# tile: it is a tighter lockup than the wide mark, with slightly smaller rings.
IC_SW, IC_RING = 3.7, 4.9
IC_A, IC_D = (23.0, 47.5), (52.0, 35.5)
IC_BAR_L = (14.5, 22.0, 59.0)
IC_BAR_R = (57.5, 24.5, 54.5)
IC_FILL = 0.69             # mark width as a share of the tile

GRAD_IN, GRAD_OUT = 0.34, 0.74   # where the stroke turns from green to white


def _mix(c1, c2, t):
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    t = t * t * (3 - 2 * t)                       # smoothstep
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _seg(d, p, q, colour, w):
    d.line([p, q], fill=colour, width=max(1, int(round(w))))
    for pt in (p, q):
        d.ellipse([pt[0] - w / 2, pt[1] - w / 2, pt[0] + w / 2, pt[1] + w / 2], fill=colour)


def _grad_seg(d, p, q, c1, c2, w, steps=90):
    for i in range(steps):
        t0, t1 = i / steps, (i + 1) / steps
        a = (p[0] + (q[0] - p[0]) * t0, p[1] + (q[1] - p[1]) * t0)
        b = (p[0] + (q[0] - p[0]) * t1, p[1] + (q[1] - p[1]) * t1)
        t = ((t0 + t1) / 2 - GRAD_IN) / (GRAD_OUT - GRAD_IN)
        _seg(d, a, b, _mix(c1, c2, t), w)


def _ring(d, centre, colour, w, rr, punch):
    c = centre
    d.ellipse([c[0] - rr - w / 2, c[1] - rr - w / 2, c[0] + rr + w / 2, c[1] + rr + w / 2], fill=colour)
    d.ellipse([c[0] - rr + w / 2, c[1] - rr + w / 2, c[0] + rr - w / 2, c[1] + rr - w / 2], fill=punch)


def draw_wide(px_width=1608, bg=None, ss=4):
    """The full four-node mark. Transparent background unless `bg` is given."""
    k = px_width / 268.0 * ss
    W, H = int(268 * k), int(148 * k)
    punch = (bg + (255,)) if bg else (0, 0, 0, 0)
    img = Image.new("RGBA", (W, H), punch)
    d = ImageDraw.Draw(img)
    S = lambda p: (p[0] * k, p[1] * k)
    w, rr, dr = SW * k, RING_R * k, DOT_R * k

    _seg(d, S(A), S(B), GREEN, w)
    _grad_seg(d, S(B), S(C), GREEN, WHITE, w)
    _seg(d, S(C), S(D), WHITE, w)
    _seg(d, S((BAR_L[0], BAR_L[1])), S((BAR_L[0], BAR_L[2])), GREEN, w)
    _seg(d, S((BAR_R[0], BAR_R[1])), S((BAR_R[0], BAR_R[2])), WHITE, w)
    _ring(d, S(A), GREEN, w, rr, punch)
    _ring(d, S(D), WHITE, w, rr, punch)
    for centre, colour in ((B, GREEN), (C, WHITE)):
        c = S(centre)
        d.ellipse([c[0] - dr, c[1] - dr, c[0] + dr, c[1] + dr], fill=colour)
    return img.resize((int(W / ss), int(H / ss)), Image.LANCZOS)


def draw_icon(px=1024, tile=TILE, radius_ratio=0.222, ss=4):
    """The app icon: two-node mark, auto-fitted and centred on a rounded tile."""
    S = int(px * ss)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * radius_ratio, fill=tile + (255,))

    pad = IC_SW / 2
    x0 = min(IC_BAR_L[0], IC_A[0] - IC_RING) - pad
    x1 = max(IC_BAR_R[0], IC_D[0] + IC_RING) + pad
    y0 = min(IC_BAR_L[1], IC_BAR_R[1], IC_D[1] - IC_RING) - pad
    y1 = max(IC_BAR_L[2], IC_BAR_R[2], IC_A[1] + IC_RING) + pad
    k = S * IC_FILL / (x1 - x0)
    ox = (S - (x1 - x0) * k) / 2 - x0 * k
    oy = (S - (y1 - y0) * k) / 2 - y0 * k
    P = lambda p: (p[0] * k + ox, p[1] * k + oy)
    w, rr = IC_SW * k, IC_RING * k

    _grad_seg(d, P(IC_A), P(IC_D), GREEN, WHITE, w)
    _seg(d, P((IC_BAR_L[0], IC_BAR_L[1])), P((IC_BAR_L[0], IC_BAR_L[2])), GREEN, w)
    _seg(d, P((IC_BAR_R[0], IC_BAR_R[1])), P((IC_BAR_R[0], IC_BAR_R[2])), WHITE, w)
    _ring(d, P(IC_A), GREEN, w, rr, tile + (255,))
    _ring(d, P(IC_D), WHITE, w, rr, tile + (255,))
    return img.resize((px, px), Image.LANCZOS)


def svg_wide(uid="tl"):
    """Inline SVG of the wide mark — crisp at any size, no raster involved."""
    hole = RING_R - SW / 2
    return f'''<svg viewBox="0 0 268 148" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Trade.LINK">
  <defs>
    <linearGradient id="{uid}g" gradientUnits="userSpaceOnUse" x1="{B[0]}" y1="{B[1]}" x2="{C[0]}" y2="{C[1]}">
      <stop offset="{GRAD_IN}" stop-color="#2FDAA6"></stop>
      <stop offset="{GRAD_OUT}" stop-color="#FBFAFB"></stop>
    </linearGradient>
    <mask id="{uid}m">
      <rect x="0" y="0" width="268" height="148" fill="#fff"></rect>
      <circle cx="{A[0]}" cy="{A[1]}" r="{hole}" fill="#000"></circle>
      <circle cx="{D[0]}" cy="{D[1]}" r="{hole}" fill="#000"></circle>
    </mask>
  </defs>
  <g mask="url(#{uid}m)" stroke-width="{SW}" stroke-linecap="round" stroke-linejoin="round">
    <path d="M{A[0]} {A[1]} L{B[0]} {B[1]}" stroke="#2FDAA6"></path>
    <path d="M{B[0]} {B[1]} L{C[0]} {C[1]}" stroke="url(#{uid}g)"></path>
    <path d="M{C[0]} {C[1]} L{D[0]} {D[1]}" stroke="#FBFAFB"></path>
    <path d="M{BAR_L[0]} {BAR_L[1]} L{BAR_L[0]} {BAR_L[2]}" stroke="#2FDAA6"></path>
    <path d="M{BAR_R[0]} {BAR_R[1]} L{BAR_R[0]} {BAR_R[2]}" stroke="#FBFAFB"></path>
    <circle cx="{A[0]}" cy="{A[1]}" r="{RING_R}" stroke="#2FDAA6"></circle>
    <circle cx="{D[0]}" cy="{D[1]}" r="{RING_R}" stroke="#FBFAFB"></circle>
    <circle cx="{B[0]}" cy="{B[1]}" r="{DOT_R}" fill="#2FDAA6" stroke="none"></circle>
    <circle cx="{C[0]}" cy="{C[1]}" r="{DOT_R}" fill="#FBFAFB" stroke="none"></circle>
  </g>
</svg>'''


# ----------------------------------------------------------------------
# CLI: regenerate the shipped assets from the vector geometry above.
#   python tools/make_logo.py            -> assets/icon.ico
#   python tools/make_logo.py --png      -> also 1024px icon + wide mark PNGs
# Dev-time only; Pillow is not a runtime dependency of the app.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path

    assets = Path(__file__).resolve().parent.parent / "assets"
    assets.mkdir(exist_ok=True)

    sizes = [256, 128, 64, 48, 32, 24, 16]
    frames = [draw_icon(s) for s in sizes]     # each frame drawn at its own size
    frames[0].save(assets / "icon.ico", format="ICO",
                   sizes=[(s, s) for s in sizes], append_images=frames[1:])
    print(f"wrote {assets / 'icon.ico'} ({', '.join(str(s) for s in sizes)} px)")

    if "--png" in sys.argv:
        draw_icon(1024).save(assets / "logo-icon.png", optimize=True)
        draw_wide(1608).save(assets / "logo-wide.png", optimize=True)
        print(f"wrote {assets / 'logo-icon.png'} and {assets / 'logo-wide.png'}")
