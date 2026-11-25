import argparse
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile

try:
    import pcbnew
except ImportError:
    sys.exit("pcbnew module not found. Run the script with the Python from your KiCad install.")

BG = "#1a1a1a"
BG_FF = "0x1a1a1a"


def find_exe(name):
    p = shutil.which(name)
    if p:
        return p
    d = os.path.dirname(sys.executable)
    for c in (name, name + ".exe"):
        p = os.path.join(d, c)
        if os.path.exists(p):
            return p
    return None


def make_converter(width):
    try:
        import cairosvg
        return lambda s, p: cairosvg.svg2png(url=s, write_to=p, output_width=width,
                                             background_color=BG)
    except Exception:
        pass
    exe = find_exe("rsvg-convert")
    if exe:
        return lambda s, p: subprocess.run(
            [exe, "-w", str(width), "-b", BG, s, "-o", p], check=True)
    exe = find_exe("inkscape")
    if exe:
        return lambda s, p: subprocess.run(
            [exe, s, "-w", str(width), "--export-background=" + BG,
             "--export-background-opacity=1", "--export-filename=" + p],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    exe = find_exe("magick")
    if exe:
        return lambda s, p: subprocess.run(
            [exe, "-background", BG, "-density", "200", s, "-resize", f"{width}x",
             "-flatten", p], check=True)
    sys.exit("No SVG->PNG converter found (inkscape / rsvg-convert / ImageMagick / cairosvg).")


def png_size(path):
    with open(path, "rb") as f:
        d = f.read(24)
    return struct.unpack(">II", d[16:24])


def run_ff(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        sys.exit("ffmpeg error:\n" + r.stderr.decode("utf-8", "replace")[-1500:])


def add_anchor(board, x0, y0, x1, y1):
    s = pcbnew.PCB_SHAPE(board)
    seg = getattr(pcbnew, "SHAPE_T_SEGMENT", None)
    if seg is None:
        seg = getattr(pcbnew, "S_SEGMENT")
    s.SetShape(seg)
    s.SetLayer(pcbnew.Cmts_User)
    try:
        s.SetStart(pcbnew.VECTOR2I(int(x0), int(y0)))
        s.SetEnd(pcbnew.VECTOR2I(int(x1), int(y1)))
    except Exception:
        s.SetStart(pcbnew.wxPoint(int(x0), int(y0)))
        s.SetEnd(pcbnew.wxPoint(int(x1), int(y1)))
    s.SetWidth(10000)
    board.Add(s)


def item_pos(it):
    if hasattr(it, "GetStart") and hasattr(it, "GetEnd"):
        a, b = it.GetStart(), it.GetEnd()
        return (a.x + b.x) / 2.0, (a.y + b.y) / 2.0
    p = it.GetPosition()
    return float(p.x), float(p.y)


def d2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def fp_area(f):
    try:
        b = f.GetBoundingBox(False)
    except Exception:
        try:
            b = f.GetBoundingBox(False, False)
        except Exception:
            return 0
    return b.GetWidth() * b.GetHeight()


def as_text(d):
    try:
        if d.GetClass() not in ("PCB_TEXT", "PTEXT"):
            return None
    except Exception:
        return None
    try:
        d = pcbnew.Cast_to_PCB_TEXT(d)
    except Exception:
        pass
    if hasattr(d, "GetText") and hasattr(d, "SetText"):
        return d
    return None


def is_power_net(name):
    u = name.upper()
    if any(k in u for k in ("GND", "VCC", "VDD", "VSS", "VBUS", "3V3", "5V")):
        return True
    return u.lstrip("/").startswith(("+", "-"))


def main():
    ap = argparse.ArgumentParser(description="Routing timelapse from a .kicad_pcb")
    ap.add_argument("pcb")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--duration", type=float, default=60.0, help="approximate video length in seconds (default 60)")
    ap.add_argument("--hold", type=float, default=3.0, help="hold on the final frame, seconds")
    ap.add_argument("--steps", type=int, default=220, help="approximate number of render steps (more = smoother but slower)")
    ap.add_argument("--zoom", type=float, default=2.8, help="base camera zoom")
    ap.add_argument("--overview-every", type=int, default=16, help="on average, pull back to the overview every N steps")
    ap.add_argument("--drift", type=float, default=0.0, help="camera 'hand shake' (off by default; 0.002-0.004 if you want it)")
    ap.add_argument("--text-steps", type=int, default=90, help="total typing steps for board text (texts are typed one by one)")
    ap.add_argument("--min-frames", type=int, default=6, help="minimum frames per step")
    ap.add_argument("--seed", type=int, default=None, help="random seed (for reproducibility)")
    ap.add_argument("--no-zones", action="store_true", help="skip copper pours entirely")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1920, help="video width")
    ap.add_argument("--board-color", default="green", choices=["green", "red", "blue", "black", "white", "yellow", "purple"], help="solder mask color in the final 3D spin (default green)")
    ap.add_argument("--spin-seconds", type=float, default=6.0, help="length of the final 360 degree 3D spin, 0 disables it (default 6)")
    ap.add_argument("--spin-frames", type=int, default=120, help="number of 3D frames rendered for the spin (more = smoother, slower)")
    ap.add_argument("--spin-tilt", type=float, default=35.0, help="camera tilt during the spin, degrees")
    ap.add_argument("--spin-zoom", type=float, default=1.0, help="camera zoom during the spin")
    ap.add_argument("--spin-quality", default="basic", choices=["basic", "high"], help="kicad-cli 3D render quality")
    ap.add_argument("--layers", default="B.Cu,F.Cu,B.Silkscreen,F.Silkscreen,Edge.Cuts")
    ap.add_argument("--theme", default=None)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.pcb):
        sys.exit(f"File not found: {args.pcb}")
    cli = find_exe("kicad-cli")
    ffmpeg = find_exe("ffmpeg")
    if not cli:
        sys.exit("kicad-cli not found.")
    if not ffmpeg:
        sys.exit("ffmpeg not found in PATH.")

    rnd = random.Random(args.seed)
    fps = args.fps
    out_w = args.width - args.width % 2
    out_h = int(out_w * 9 / 16)
    out_h -= out_h % 2
    cw, ch = out_w * 2, out_h * 2
    out = args.output or os.path.splitext(args.pcb)[0] + "_timelapse.mp4"
    convert = make_converter(cw)

    board = pcbnew.LoadBoard(args.pcb)
    bb = board.ComputeBoundingBox(False)
    bx, by, bw, bh = bb.GetX(), bb.GetY(), max(1, bb.GetWidth()), max(1, bb.GetHeight())

    plotted = set()
    for n in [x.strip() for x in args.layers.split(",") if x.strip()]:
        for cand in (n, n.replace("Silkscreen", "SilkS")):
            try:
                i = board.GetLayerID(cand)
            except Exception:
                i = -1
            if i is not None and i >= 0:
                plotted.add(i)
                break

    fps_list = list(board.GetFootprints())
    tracks = list(board.GetTracks())
    zones = [] if args.no_zones else list(board.Zones())
    texts = []
    for d in board.GetDrawings():
        t = as_text(d)
        if t is not None and t.GetLayer() in plotted and t.GetText().strip():
            texts.append(t)

    ref_idx = []
    for i, f in enumerate(fps_list):
        try:
            r = f.Reference()
            if r.IsVisible():
                ref_idx.append(i)
                r.SetVisible(False)
        except Exception:
            pass

    print(f"Footprints: {len(fps_list)}, tracks/vias: {len(tracks)}, "
          f"zones: {len(zones)}, texts: {len(texts)}")

    for it in fps_list + tracks + zones + texts:
        board.Remove(it)
    add_anchor(board, bx, by, bx + bw, by + bh)

    tmp = tempfile.mkdtemp(prefix="pcbtl_")
    counter = [0]
    layers_state = {"v": args.layers}

    def export_svg(pcb_tmp, svg):
        for attempt in range(2):
            cmd = [cli, "pcb", "export", "svg", "--layers", layers_state["v"],
                   "--page-size-mode", "2"]
            if args.theme:
                cmd += ["--theme", args.theme]
            cmd += ["-o", svg, pcb_tmp]
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r.returncode == 0 and os.path.exists(svg):
                return
            if attempt == 0 and "Silkscreen" in layers_state["v"]:
                layers_state["v"] = layers_state["v"].replace("Silkscreen", "SilkS")
                continue
            sys.exit("kicad-cli failed to export SVG:\n" +
                     r.stderr.decode("utf-8", "replace")[-1000:])

    def snap():
        pcb_tmp = os.path.join(tmp, "step.kicad_pcb")
        svg = os.path.join(tmp, f"s{counter[0]:04d}.svg")
        png = os.path.join(tmp, f"s{counter[0]:04d}.png")
        board.Save(pcb_tmp)
        export_svg(pcb_tmp, svg)
        convert(svg, png)
        os.remove(svg)
        counter[0] += 1
        return png

    def geom(png):
        pw, ph = png_size(png)
        s = min(cw / pw, ch / ph)
        w, h = pw * s, ph * s
        return (cw - w) / 2.0, (ch - h) / 2.0, w, h

    def to_canvas(g, x, y):
        ox, oy, w, h = g
        fx = min(1.0, max(0.0, (x - bx) / bw))
        fy = min(1.0, max(0.0, (y - by) / bh))
        return (ox + fx * w) / cw, (oy + fy * h) / ch

    def clamp_cam(cx, cy, z):
        lo = 0.5 / z
        return (min(1 - lo, max(lo, cx)), min(1 - lo, max(lo, cy)), z)

    OV = (0.5, 0.5, 1.0)

    def pick_cam(g, pts, cam, zfix=None):
        cs = [to_canvas(g, x, y) for x, y in pts]
        xs = [c[0] for c in cs]
        ys = [c[1] for c in cs]
        mx, my = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
        z = zfix if zfix else args.zoom * rnd.uniform(0.85, 1.25)
        z = max(1.0, min(z, 0.6 / span))
        cx, cy, cz = cam
        half = 0.5 / cz
        if (zfix is None and cz > 1.3 and abs(mx - cx) < half * 0.55
                and abs(my - cy) < half * 0.55 and span < half * 0.8):
            return cam
        return clamp_cam(mx, my, z)

    clips = []
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-video_track_timescale", "90000"]

    def make_clip(png, cam0, cam1, n, move):
        idx = len(clips)
        clip = os.path.join(tmp, f"c{idx:04d}.mp4")
        t = f"min(on/{max(move, 1)},1)"
        e = f"({t}*{t}*(3-2*{t}))"
        z = f"({cam0[2]:.5f}+({cam1[2]:.5f}-{cam0[2]:.5f})*{e})"
        amp = args.drift if max(cam0[2], cam1[2]) > 1.2 else 0.0
        ph = rnd.uniform(0, 6.28)
        p1 = rnd.uniform(25, 45)
        p2 = p1 * 1.37
        dx = f"{amp:.5f}*sin(on/{p1:.1f}+{ph:.2f})"
        dy = f"{amp:.5f}*cos(on/{p2:.1f}+{ph:.2f})"
        cx = f"({cam0[0]:.5f}+({cam1[0]:.5f}-{cam0[0]:.5f})*{e}+{dx})"
        cy = f"({cam0[1]:.5f}+({cam1[1]:.5f}-{cam0[1]:.5f})*{e}+{dy})"
        vf = (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
              f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color={BG_FF},"
              f"zoompan=z='{z}':x='{cx}*iw-iw/{z}/2':y='{cy}*ih-ih/{z}/2'"
              f":d={n}:s={out_w}x{out_h}:fps={fps},format=yuv420p")
        run_ff([ffmpeg, "-y", "-i", png, "-vf", vf, "-frames:v", str(n)] + enc + [clip])
        clips.append(clip)

    def make_fade_clip(png_a, png_b, pre, fade, post):
        idx = len(clips)
        clip = os.path.join(tmp, f"c{idx:04d}.mp4")
        base = (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color={BG_FF},"
                f"scale={out_w}:{out_h},setsar=1,format=yuv420p")
        fc = (f"[0:v]{base}[a];[1:v]{base}[b];"
              f"[a][b]xfade=transition=fade:duration={fade}:offset={pre}")
        run_ff([ffmpeg, "-y",
                "-loop", "1", "-framerate", str(fps), "-t", f"{pre + fade:.3f}", "-i", png_a,
                "-loop", "1", "-framerate", str(fps), "-t", f"{fade + post:.3f}", "-i", png_b,
                "-filter_complex", fc, "-r", str(fps)] + enc + [clip])
        clips.append(clip)

    def nn_order(pts, jump=0.12, start=None):
        n = len(pts)
        if n == 0:
            return []
        rest = set(range(n))
        cur = start if start is not None else min(rest, key=lambda i: pts[i])
        out = [cur]
        rest.discard(cur)
        while rest:
            if jump > 0 and rnd.random() < jump:
                cur = rnd.choice(sorted(rest))
            else:
                p = pts[out[-1]]
                cur = min(rest, key=lambda i: d2(pts[i], p))
            out.append(cur)
            rest.discard(cur)
        return out

    mask_names = {"green": "Green", "red": "Red", "blue": "Blue", "black": "Black",
                  "white": "White", "yellow": "Yellow", "purple": "Purple"}

    def apply_board_color(name):
        try:
            ds = board.GetDesignSettings()
            sd = ds.GetStackupDescriptor()
            if not list(sd.GetList()):
                sd.BuildDefaultStackupList(ds, board.GetCopperLayerCount())
            try:
                ds.m_HasStackup = True
            except Exception:
                pass
            hit = False
            for item in sd.GetList():
                if item.GetType() == pcbnew.BS_ITEM_TYPE_SOLDERMASK:
                    item.SetColor(mask_names[name])
                    hit = True
            return hit
        except Exception as ex:
            print("\nWarning: could not set board color:", ex)
            return False

    def add_spin(last_png):
        if args.spin_seconds <= 0:
            return
        use_stackup = False
        if args.board_color != "green":
            use_stackup = apply_board_color(args.board_color)
            if not use_stackup:
                print("Falling back to the default green board")
        for d in list(board.GetDrawings()):
            try:
                if d.GetLayer() == pcbnew.Cmts_User:
                    board.Remove(d)
            except Exception:
                pass
        final_pcb = os.path.join(tmp, "final.kicad_pcb")
        board.Save(final_pcb)
        frames = max(12, args.spin_frames)
        rate = frames / args.spin_seconds
        prj_dir = os.path.dirname(os.path.abspath(args.pcb))
        for k in range(frames):
            raw = os.path.join(tmp, f"raw_{k:04d}.png")
            cmd = [cli, "pcb", "render", "--width", str(out_w), "--height", str(out_h),
                   "--background", "transparent", "--quality", args.spin_quality,
                   "--zoom", str(args.spin_zoom),
                   "--rotate", f"{-args.spin_tilt:.2f},0,{360.0 * k / frames:.2f}",
                   "-D", "KIPRJMOD=" + prj_dir, "-o", raw]
            if use_stackup:
                cmd.append("--use-board-stackup-colors")
            cmd.append(final_pcb)
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r.returncode != 0 or not os.path.exists(raw):
                print("\nSkipping the 3D spin, kicad-cli pcb render failed (needs KiCad 9+):\n"
                      + r.stderr.decode("utf-8", "replace")[-600:])
                return
            print(f"\r3D frame {k + 1}/{frames}", end="", flush=True)
        print()
        flat = os.path.join(tmp, "flat_%04d.png")
        fc = (f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[a];"
              f"[1:v][a]overlay=(W-w)/2:(H-h)/2:shortest=1,format=rgb24")
        run_ff([ffmpeg, "-y", "-framerate", f"{rate:.4f}",
                "-i", os.path.join(tmp, "raw_%04d.png").replace("%04d", "%04d"),
                "-f", "lavfi", "-i", f"color=c={BG_FF}:s={out_w}x{out_h}:r={rate:.4f}",
                "-filter_complex", fc, "-start_number", "0", flat])
        make_fade_clip(last_png, os.path.join(tmp, "flat_0000.png"), 0.1, 0.8, 0.1)
        clip = os.path.join(tmp, f"c{len(clips):04d}.mp4")
        run_ff([ffmpeg, "-y", "-framerate", f"{rate:.4f}", "-i", flat,
                "-vf", f"fps={fps},format=yuv420p"] + enc + [clip])
        clips.append(clip)

    S = max(20, args.steps)
    steps = []

    def make_add(items):
        def fn():
            for it in items:
                board.Add(it)
        return fn

    if fps_list:
        pos = [item_pos(f) for f in fps_list]
        area = [fp_area(f) for f in fps_list]
        by_area = sorted(range(len(fps_list)), key=lambda i: -area[i])
        k = max(1, min(8, len(fps_list) // 10))
        order = by_area[:k]
        rest_idx = by_area[k:]
        if rest_idx:
            sub = [pos[i] for i in rest_idx]
            st = min(range(len(sub)), key=lambda m: d2(sub[m], pos[order[-1]]))
            order += [rest_idx[m] for m in nn_order(sub, 0.10, st)]
        chunk_c = max(1, math.ceil(len(order) / max(1, int(S * 0.33))))
        for i in range(0, len(order), chunk_c):
            items = [fps_list[j] for j in order[i:i + chunk_c]]
            steps.append({"phase": "comp", "w": 1.0 * math.sqrt(len(items)),
                          "pts": [item_pos(f) for f in items], "fn": make_add(items)})

    if tracks:
        nets = {}
        for t in tracks:
            nets.setdefault(t.GetNetname(), []).append(t)
        chained = {}
        for name, its in nets.items():
            pts = [item_pos(t) for t in its]
            idx = nn_order(pts, 0.0)
            chained[name] = ([its[k] for k in idx], [pts[k] for k in idx])

        def centroid(name):
            p = chained[name][1]
            return (sum(a[0] for a in p) / len(p), sum(a[1] for a in p) / len(p))

        sig = [n for n in chained if not is_power_net(n)]
        pwr = [n for n in chained if is_power_net(n)]
        net_seq = []
        for grp in (sig, pwr):
            if grp:
                cents = [centroid(n) for n in grp]
                net_seq += [grp[i] for i in nn_order(cents, 0.15)]

        chunk_t = max(1, math.ceil(len(tracks) / max(1, int(S * 0.50))))
        groups = []
        pend_i, pend_p = [], []

        def flush():
            nonlocal pend_i, pend_p
            if pend_i:
                groups.append((pend_i, pend_p))
            pend_i, pend_p = [], []

        for name in net_seq:
            its, pts = chained[name]
            for i in range(0, len(its), chunk_t):
                pi, pp = its[i:i + chunk_t], pts[i:i + chunk_t]
                if len(pi) == chunk_t:
                    flush()
                    groups.append((pi, pp))
                else:
                    pend_i += pi
                    pend_p += pp
                    if len(pend_i) >= chunk_t:
                        flush()
        flush()
        for its, pts in groups:
            steps.append({"phase": "track", "w": 0.7 * math.sqrt(len(its)),
                          "pts": pts, "fn": make_add(its)})

    if texts:
        tp = [item_pos(t) for t in texts]
        order_t = nn_order(tp, 0.05)
        budget = max(len(texts), args.text_steps)
        per = max(2, min(8, budget // len(texts)))

        def make_type(t, s, L, first):
            def fn():
                t.SetText(s[:L])
                if first:
                    board.Add(t)
            return fn

        for j in order_t:
            t = texts[j]
            s = t.GetText()
            n = len(s)
            kk = min(per, n)
            lens = sorted(set(max(1, math.ceil(n * (q + 1) / kk)) for q in range(kk)))
            for qi, L in enumerate(lens):
                steps.append({"phase": "text", "w": 0.5, "pts": [tp[j]],
                              "fn": make_type(t, s, L, qi == 0)})

    zone_groups = []
    if zones:
        gs = max(1, math.ceil(len(zones) / 5))
        zone_groups = [zones[i:i + gs] for i in range(0, len(zones), gs)]
        for zg in zone_groups:
            steps.append({"zone": True, "phase": "zone", "zs": zg})

    if ref_idx:
        rp = []
        for i in ref_idx:
            p = fps_list[i].Reference().GetPosition()
            rp.append((float(p.x), float(p.y)))
        order = nn_order(rp, 0.06)
        chunk_r = max(1, math.ceil(len(order) / max(1, int(S * 0.12))))

        def make_reveal(idxs):
            def fn():
                for i in idxs:
                    try:
                        fps_list[i].Reference().SetVisible(True)
                    except Exception:
                        pass
            return fn

        for i in range(0, len(order), chunk_r):
            sel = order[i:i + chunk_r]
            steps.append({"phase": "label", "w": 0.6 * math.sqrt(len(sel)),
                          "pts": [rp[j] for j in sel],
                          "fn": make_reveal([ref_idx[j] for j in sel])})

    normal = [s for s in steps if not s.get("zone")]
    nsteps = len(normal)
    zone_cost = 1.9 * len(zone_groups)
    body_sec = max(8.0, args.duration - args.hold - 1.0 - 1.5 - zone_cost)
    body_frames = int(fps * body_sec)

    for s in normal:
        f = rnd.uniform(0.6, 1.5)
        r = rnd.random()
        if r < 0.07:
            f *= 2.4
        elif r < 0.17:
            f *= 0.5
        s["w"] *= f
    tot = sum(s["w"] for s in normal) or 1.0
    for s in normal:
        s["n"] = max(args.min_frames, int(round(body_frames * s["w"] / tot)))

    print(f"Steps: {nsteps}, about {sum(s['n'] for s in normal) / fps:.1f} s of main part")

    png = snap()
    make_clip(png, OV, OV, fps, 1)
    cam = OV
    last_phase = None
    since_ov = 0
    next_ov = max(3, int(args.overview_every * rnd.uniform(0.6, 1.4)))
    done = 0

    for s in steps:
        if s.get("zone"):
            if cam != OV:
                make_clip(png, cam, OV, int(1.2 * fps), int(1.0 * fps))
            cam = OV
            for z in s["zs"]:
                board.Add(z)
            try:
                pcbnew.ZONE_FILLER(board).Fill(board.Zones())
            except Exception as ex:
                print("\nWarning: failed to fill zones:", ex)
            png2 = snap()
            make_fade_clip(png, png2, 0.2, 1.3, 0.4)
            png = png2
            last_phase = "zone"
            since_ov = 0
            continue

        s["fn"]()
        png = snap()
        g = geom(png)
        n = s["n"]

        force_ov = done > 0 and (s["phase"] != last_phase or since_ov >= next_ov)
        if force_ov and last_phase != "zone":
            new = OV
            since_ov = 0
            next_ov = max(3, int(args.overview_every * rnd.uniform(0.6, 1.4)))
        elif rnd.random() < 0.08:
            new = pick_cam(g, s["pts"], cam, zfix=max(1.3, args.zoom * 0.5))
            since_ov += 1
        else:
            new = pick_cam(g, s["pts"], cam)
            since_ov += 1
        last_phase = s["phase"]

        move = 1 if new == cam else max(4, int(n * rnd.uniform(0.5, 0.85)))
        make_clip(png, cam, new, n, move)
        cam = new
        done += 1
        print(f"\rstep {done}/{nsteps}", end="", flush=True)
    print()

    move_final = int(1.5 * fps)
    make_clip(png, cam, OV, move_final + int(args.hold * fps), move_final)
    add_spin(png)

    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for c in clips:
            f.write("file '" + c.replace("\\", "/") + "'\n")
    run_ff([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])

    if args.keep:
        print("Temp files:", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    print("Done:", out)


if __name__ == "__main__":
    main()
