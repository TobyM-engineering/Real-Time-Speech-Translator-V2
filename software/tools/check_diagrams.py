#!/usr/bin/env python3
"""Audit every repo SVG for text legibility and overlap at its rendered size."""
import re, sys, pathlib, subprocess, math

ROOT = pathlib.Path("/home/translator2/translator")
GH_MAX = 800          # GitHub's content column, approximately

# --- how is each svg embedded? -> displayed width in CSS px
embed = {}
for md in subprocess.run(['git','ls-files','*.md'],cwd=ROOT,capture_output=True,text=True).stdout.split():
    t = (ROOT/md).read_text()
    for m in re.finditer(r'<img[^>]*src="([^"]+\.svg)"[^>]*>', t):
        tag, src = m.group(0), m.group(1)
        w = re.search(r'width="(\d+)"', tag)
        target = (ROOT/md).parent / src
        embed.setdefault(target.resolve(), []).append(int(w.group(1)) if w else None)

def styles(svg):
    """class -> font-size from the <style> block"""
    out = {}
    for blk in re.findall(r'<style>(.*?)</style>', svg, re.S):
        for sel, body in re.findall(r'\.([\w-]+)\s*\{([^}]*)\}', blk):
            fs = re.search(r'font-size:\s*([\d.]+)px', body)
            if fs: out[sel] = float(fs.group(1))
    return out

def spans(svg):
    """[(start, end, transform)] for every <g transform=...> block."""
    out, stack = [], []
    # every <g> must be tracked, transform or not — otherwise a plain <g>'s
    # closing tag pops the wrong entry and the stack desynchronises
    for m in re.finditer(r'<g\b([^>]*)>|</g>', svg):
        if m.group(0).startswith('</g'):
            if stack:
                st, tf = stack.pop()
                if tf: out.append((st, m.start(), tf))
        else:
            tfm = re.search(r'transform="([^"]+)"', m.group(1) or '')
            stack.append((m.end(), tfm.group(1) if tfm else None))
    return out

def apply_tf(x, y, tf):
    r = re.match(r'rotate\(\s*([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s*\)', tf)
    if r:
        a, cx, cy = (float(v) for v in r.groups()); a = math.radians(a)
        dx, dy = x - cx, y - cy
        return cx + dx*math.cos(a) - dy*math.sin(a), cy + dx*math.sin(a) + dy*math.cos(a)
    t = re.match(r'translate\(\s*([\d.-]+)[ ,]+([\d.-]+)\s*\)', tf)
    if t:
        return x + float(t.group(1)), y + float(t.group(2))
    return x, y

def texts(svg, cls):
    """(x, y, size, anchor, content, rotated) for every <text>, transform-aware."""
    res = []
    grp = spans(svg)
    for m in re.finditer(r'<text([^>]*)>(.*?)</text>', svg, re.S):
        at, body = m.group(1), re.sub(r'<[^>]+>', '', m.group(2))
        fs = re.search(r'font-size="([\d.]+)"', at)
        size = float(fs.group(1)) if fs else None
        if size is None:
            c = re.search(r'class="([^"]+)"', at)
            if c:
                for k in c.group(1).split():
                    if k in cls: size = cls[k]; break
        if size is None: size = 16.0
        x = re.search(r'\bx="(-?[\d.]+)"', at); y = re.search(r'\by="(-?[\d.]+)"', at)
        if not (x and y): continue
        anc = re.search(r'text-anchor="(\w+)"', at)
        px, py = float(x.group(1)), float(y.group(1))
        rot = False
        for s, e, tf in grp:
            if s <= m.start() < e:
                px, py = apply_tf(px, py, tf)
                if tf.startswith('rotate'): rot = True
        res.append((px, py, size, anc.group(1) if anc else 'start',
                    body.strip(), rot))
    return res

def box(t):
    x, y, s, anc, body, rot = t
    w = len(body) * s * 0.52          # mean advance for a sans face
    # offsets of the box from the anchor point, before any rotation
    if   anc == 'middle': dx0, dx1 = -w/2, w/2
    elif anc == 'end':    dx0, dx1 = -w, 0.0
    else:                 dx0, dx1 = 0.0, w
    dy0, dy1 = -s*0.78, s*0.24
    if rot:                            # 180 degrees negates both offsets
        dx0, dx1 = -dx1, -dx0
        dy0, dy1 = -dy1, -dy0
    return (x + dx0, y + dy0, x + dx1, y + dy1)


def overlap(a, b):
    ax0,ay0,ax1,ay1 = a; bx0,by0,bx1,by1 = b
    ix = min(ax1,bx1) - max(ax0,bx0); iy = min(ay1,by1) - max(ay0,by0)
    if ix <= 0 or iy <= 0: return 0.0
    return ix*iy / min((ax1-ax0)*(ay1-ay0), (bx1-bx0)*(by1-by0))

print(f"{'file':46s} {'canvas':>10s} {'shown':>6s} {'scale':>6s} {'min px':>7s}  ovl  off")
print("-"*104)
fails = []
for f in sorted(subprocess.run(['git','ls-files','*.svg'],cwd=ROOT,capture_output=True,text=True).stdout.split()):
    p = ROOT/f
    svg = p.read_text()
    vb = re.search(r'viewBox="[\d.\- ]*?([\d.]+) ([\d.]+)"', svg)
    cw, ch = (float(vb.group(1)), float(vb.group(2))) if vb else (0,0)
    shown = embed.get(p.resolve(), [None])[0]
    disp = shown if shown else min(cw, GH_MAX)
    scale = disp/cw if cw else 1
    cls = styles(svg); ts = texts(svg, cls)
    if not ts: continue
    eff = min(t[2]*scale for t in ts)
    boxes = [(box(t), t) for t in ts]
    ov = []
    for i in range(len(boxes)):
        for j in range(i+1, len(boxes)):
            r = overlap(boxes[i][0], boxes[j][0])
            if r > 0.30 and boxes[i][1][5] == boxes[j][1][5]:
                ov.append((boxes[i][1][4][:20], boxes[j][1][4][:20], round(r,2)))
    # text running off the canvas — for the screen mockups this means text
    # leaving the physical display, which no real device would render
    oob = []
    for b, t in boxes:
        if b[0] < -1 or b[2] > cw + 1 or b[1] < -1 or b[3] > ch + 1:
            oob.append((t[4][:26], round(b[0]), round(b[2])))
    bad = eff < 11.0 or len(ov) > 0 or len(oob) > 0
    if bad: fails.append((f, round(eff,1), len(ov), ov[:3], oob[:4]))
    print(f"{f:46s} {int(cw)}x{int(ch):<5d} {int(disp):>6d} {scale:>6.2f} {eff:>7.1f}  {len(ov)}  {len(oob)}{'   <-- FAIL' if bad else ''}")
print()
for f, eff, n, sample, oob in fails:
    print(f"FAIL {f}: min effective {eff}px, {n} overlapping pair(s), {len(oob)} off-canvas")
    for a,b,r in sample: print(f"       overlap '{a}' x '{b}'  ({int(r*100)}%)")
    for t,x0,x1 in oob: print(f"       off-canvas '{t}'  spans x {x0}..{x1}")

