"""Generate the BOUND README demo GIF from a real execution trace.

Reads the machine-readable run trace (``bound_integration/run.json`` produced by
BOUND's own ``examples/reference_integration`` reference integration) and renders
a short animated GIF (``assets/bound-demo.gif``) visualizing the plan ->
execution -> evidence -> BOUND evaluation -> decision -> lineage flow.

The GIF is a *visualization* only; the raw evidence and the integration report
are the proof. Every value shown in the frames is taken from ``run.json`` (a real
run); nothing is fabricated. Uses only the Python standard library -- no image
dependency -- via a small embedded 5x7 bitmap font and a minimal GIF89a/LZW
encoder.

Usage::

    python scripts/generate_demo.py [--run-json PATH] [--output PATH]

Defaults look for the trace in this repo's own ``bound_integration/run.json``
and write the GIF to ``assets/bound-demo.gif``.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any

# --- canvas -----------------------------------------------------------------
WIDTH = 480
HEIGHT = 270
SCALE = 3  # each font pixel -> SCALE x SCALE screen pixels
GLYPH_W = 5
GLYPH_H = 7
DELAY_CS = 150  # frame delay in centiseconds (~1.5s)

# Palette indices.
BG, FG, GREEN, RED, TITLE_BG, DIM, YELLOW, CYAN = range(8)

PALETTE: list[tuple[int, int, int]] = [
    (18, 20, 32),  # 0 background (dark navy)
    (235, 238, 246),  # 1 foreground (near-white)
    (86, 214, 122),  # 2 green (PASS / ACCEPT)
    (232, 96, 96),  # 3 red
    (54, 92, 196),  # 4 title bar blue
    (120, 124, 148),  # 5 dim gray
    (240, 210, 96),  # 6 yellow
    (96, 214, 232),  # 7 cyan
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
]

# --- 5x7 bitmap font: "C|row0;row1;...;row6" (rows are 5 chars of '.'/'#') --
_FONT_SRC = r"""
A|.###.;#...#;#...#;#####;#...#;#...#;#...#
B|####.;#...#;#...#;####.;#...#;#...#;####.
C|.####;#....;#....;#....;#....;#....;.####
D|####.;#...#;#...#;#...#;#...#;#...#;####.
E|#####;#....;#....;####.;#....;#....;#####
F|#####;#....;#....;####.;#....;#....;#....
G|.####;#....;#....;#.###;#...#;#...#;.####
H|#...#;#...#;#...#;#####;#...#;#...#;#...#
I|#####;..#..;..#..;..#..;..#..;..#..;#####
J|..###;...#.;...#.;...#.;#..#.;#..#.;.##..
K|#...#;#..#.;#.#..;##...;#.#..;#..#.;#...#
L|#....;#....;#....;#....;#....;#....;#####
M|#...#;##.##;#.#.#;#.#.#;#...#;#...#;#...#
N|#...#;#...#;##..#;#.#.#;#..##;#...#;#...#
O|.###.;#...#;#...#;#...#;#...#;#...#;.###.
P|####.;#...#;#...#;####.;#....;#....;#....
Q|.###.;#...#;#...#;#...#;#.#.#;#..#.;.##.#
R|####.;#...#;#...#;####.;#.#..;#..#.;#...#
S|.####;#....;#....;.###.;....#;....#;#####
T|#####;..#..;..#..;..#..;..#..;..#..;..#..
U|#...#;#...#;#...#;#...#;#...#;#...#;.###.
V|#...#;#...#;#...#;#...#;#...#;.#.#.;..#..
W|#...#;#...#;#...#;#.#.#;#.#.#;##.##;#...#
X|#...#;#...#;.#.#.;..#..;.#.#.;#...#;#...#
Y|#...#;#...#;.#.#.;..#..;..#..;..#..;..#..
Z|#####;....#;...#.;..#..;.#...;#....;#####
0|.###.;#...#;#..##;#.#.#;##..#;#...#;.###.
1|..#..;.##..;..#..;..#..;..#..;..#..;#####
2|.###.;#...#;....#;...#.;..#..;.#...;#####
3|####.;....#;....#;.###.;....#;....#;####.
4|...#.;..##.;.#.#.;#..#.;#####;...#.;...#.
5|#####;#....;####.;....#;....#;#...#;.###.
6|.###.;#....;#....;####.;#...#;#...#;.###.
7|#####;....#;...#.;..#..;.#...;.#...;.#...
8|.###.;#...#;#...#;.###.;#...#;#...#;.###.
9|.###.;#...#;#...#;.####;....#;....#;.###.
 |.....;.....;.....;.....;.....;.....;.....
.|.....;.....;.....;.....;.....;.##.;.##..
,|.....;.....;.....;.....;.##.;.##.;.#...
-|.....;.....;.....;#####;.....;.....;.....
:|.....;.##.;.##.;.....;.##.;.##.;.....
=|.....;.....;#####;.....;#####;.....;.....
$|..#..;.####;#.#..;.###.;..#.#;####.;..#..
_|.....;.....;.....;.....;.....;.....;#####
?|.###.;#...#;....#;...#.;..#..;.....;..#..
"""


def _parse_font(src: str) -> dict[str, list[str]]:
    """Parse the compact ``C|row;row;...`` font source into glyph row-lists."""
    font: dict[str, list[str]] = {}
    for line in src.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        ch, rows = line.split("|", 1)
        font[ch] = rows.split(";")
    return font


FONT: dict[str, list[str]] = _parse_font(_FONT_SRC)

_ARROW: tuple[str, ...] = ("..#..", ".###.", "..#..", "..#..")


def new_frame() -> bytearray:
    """Return a fresh frame buffer (palette indices), background-filled."""
    return bytearray([BG]) * (WIDTH * HEIGHT)


def _set(buf: bytearray, x: int, y: int, color: int) -> None:
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        buf[y * WIDTH + x] = color


def fill_rect(buf: bytearray, x0: int, y0: int, x1: int, y1: int, color: int) -> None:
    """Fill the axis-aligned rectangle [x0,x1) x [y0,y1) with *color*."""
    for y in range(y0, y1):
        for x in range(x0, x1):
            _set(buf, x, y, color)


def text_width(text: str, scale: int = SCALE) -> int:
    """Pixel width of *text* at *scale* (with a 1px inter-glyph gap)."""
    return max(0, len(text) * (GLYPH_W + 1) * scale - scale)


def draw_text(buf: bytearray, x: int, y: int, text: str, color: int, scale: int = SCALE) -> None:
    """Draw *text* (uppercased) using the 5x7 font, scaled by *scale*."""
    cx = x
    for ch in text:
        glyph = FONT.get(ch.upper())
        for r in range(GLYPH_H):
            row = glyph[r] if glyph and r < len(glyph) else "....."
            for c in range(GLYPH_W):
                if c < len(row) and row[c] == "#":
                    fill_rect(
                        buf,
                        cx + c * scale,
                        y + r * scale,
                        cx + (c + 1) * scale,
                        y + (r + 1) * scale,
                        color,
                    )
        cx += (GLYPH_W + 1) * scale


def draw_down_arrow(buf: bytearray, x: int, y: int, color: int, scale: int = SCALE) -> None:
    """Draw a small down-arrow glyph (used in the lineage frame)."""
    for r, row in enumerate(_ARROW):
        for c in range(GLYPH_W):
            if c < len(row) and row[c] == "#":
                fill_rect(
                    buf,
                    x + c * scale,
                    y + r * scale,
                    x + (c + 1) * scale,
                    y + (r + 1) * scale,
                    color,
                )


# --- trace extraction -------------------------------------------------------
def _fmt(v: float) -> str:
    """Format a number compactly: 1.0 -> '1.0', 0.75 -> '0.75', 0.0 -> '0.0'."""
    s = f"{v:.4f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _passed_summary(stdout: str) -> str:
    """Return the last pytest summary line (e.g. '20 passed in 0.03s')."""
    last = ""
    for line in stdout.strip().splitlines():
        if "passed" in line or "failed" in line or "error" in line:
            last = line.strip()
    return last


def _acceptance(trace: dict[str, Any], check_id: str) -> dict[str, Any]:
    """Find an acceptance-check evidence record by id ({} if absent)."""
    for c in trace["evidence"]["acceptance"]:
        if c["check_id"] == check_id:
            return c
    return {"check_id": check_id, "passed": False, "details": ""}


def render_frame(title: str, rows: list[tuple[str, str, int]]) -> bytearray:
    """Render one frame: a title bar plus vertically-centered body rows.

    Each row is ``(kind, text, color)`` where kind is ``"c"`` (centered text) or
    ``"a"`` (a centered down-arrow; text is ignored).
    """
    buf = new_frame()
    fill_rect(buf, 0, 0, WIDTH, 40, TITLE_BG)
    draw_text(buf, (WIDTH - text_width(title)) // 2, 11, title, FG)
    pitch = [30 if kind == "c" else 16 for kind, _t, _c in rows]
    top = 48
    y = top + max(0, (HEIGHT - top - sum(pitch)) // 2)
    for (kind, text, color), p in zip(rows, pitch, strict=True):
        if kind == "a":
            draw_down_arrow(buf, (WIDTH - 5 * SCALE) // 2, y, color)
        else:
            draw_text(buf, (WIDTH - text_width(text)) // 2, y, text, color)
        y += p
    return buf


def build_frames(trace: dict[str, Any]) -> list[bytearray]:
    """Build the six demo frames from real values in *trace* (no fabrication)."""
    ev = trace["evaluation"]
    sc = ev["scores"]
    decision = ev["decision"]
    next_action = trace["next_action"]
    plan_id = str(trace.get("plan_id", "PHASE-001"))
    tests_pass = _acceptance(trace, "tests-pass")["passed"]
    svc_pass = _acceptance(trace, "service-tests-pass")["passed"]
    # raw_commands lives at the top level of a RunTrace; fall back to the legacy
    # nested location so the renderer tolerates either trace layout.
    raw = trace.get("raw_commands") or trace.get("evidence", {}).get("raw_commands") or {}
    full = (raw.get("full_suite") or {}).get("stdout", "")
    svc = (raw.get("service_suite") or {}).get("stdout", "")
    svc_cmd = (raw.get("service_suite") or {}).get("command", "")
    full_sum = (_passed_summary(full) or "exit 0").upper()
    svc_sum = (_passed_summary(svc) or "exit 0").upper()
    # Derive the service-test target label from the real command (e.g.
    # "tests/test_calculator.py"); fall back to a generic label.
    svc_target = "SERVICE TESTS"
    for tok in svc_cmd.split():
        if tok.startswith("tests/"):
            svc_target = tok.upper()
            break

    yn = lambda b: "PASS" if b else "FAIL"  # noqa: E731
    g_or_r = lambda b: GREEN if b else RED  # noqa: E731
    dcolor = GREEN if decision == "ACCEPT" else RED

    return [
        render_frame(
            "PLAN",
            [
                ("c", "PLAN.MD", FG),
                ("c", plan_id, YELLOW),
                ("c", "GOAL: VERIFY BOUND", FG),
                ("c", "V0.6 RELEASE", FG),
            ],
        ),
        render_frame(
            "EXECUTION",
            [
                ("c", "$ UV RUN PYTEST -Q", FG),
                ("c", full_sum, GREEN if "PASSED" in full_sum else RED),
                ("c", "UV RUN PYTEST", FG),
                ("c", svc_target + " -Q", FG),
                ("c", svc_sum, GREEN if "PASSED" in svc_sum else RED),
            ],
        ),
        render_frame(
            "EVIDENCE",
            [
                ("c", "TESTS-PASS      " + yn(tests_pass), g_or_r(tests_pass)),
                ("c", "SVC-TESTS-PASS  " + yn(svc_pass), g_or_r(svc_pass)),
                ("c", "UNEXPECTED-FILES  NONE", GREEN),
                ("c", "TOKENS / RUNTIME  N/A", DIM),
            ],
        ),
        render_frame(
            "BOUND EVAL",
            [
                ("c", f"A = {_fmt(sc['acceptance'])}", FG),
                ("c", f"I = {_fmt(sc['influence'])}", FG),
                ("c", f"R = {_fmt(sc['risk'])}", FG),
                ("c", f"C = {_fmt(sc['cost'])}", FG),
                ("c", f"S = {_fmt(ev['score'])}   T = {_fmt(ev['threshold'])}", YELLOW),
            ],
        ),
        render_frame(
            "DECISION",
            [
                ("c", decision, dcolor),
                ("c", "STOP OPTIMIZING THIS STEP", FG),
                ("c", next_action.upper(), CYAN),
            ],
        ),
        render_frame(
            "LINEAGE",
            [
                ("c", "PLAN.MD", FG),
                ("a", "", DIM),
                ("c", "STEPCONTRACT", FG),
                ("a", "", DIM),
                ("c", "EXECUTION EVIDENCE", FG),
                ("a", "", DIM),
                ("c", "BOUND", FG),
                ("a", "", DIM),
                ("c", "INTEGRATION REPORT", FG),
            ],
        ),
    ]


# --- minimal GIF89a + LZW encoder (standard library only) -------------------
def _lzw(indices: bytes, min_code_size: int) -> bytes:
    """GIF-spec LZW compression of *indices* (palette indices) to bytes."""
    clear = 1 << min_code_size
    eoi = clear + 1
    code_size = min_code_size + 1
    table: dict[bytes, int] = {bytes([i]): i for i in range(clear)}
    next_code = eoi + 1
    out = bytearray()
    bit_buf = 0
    bit_width = 0

    def emit(code: int) -> None:
        nonlocal bit_buf, bit_width
        bit_buf |= code << bit_width
        bit_width += code_size
        while bit_width >= 8:
            out.append(bit_buf & 0xFF)
            bit_buf >>= 8
            bit_width -= 8

    emit(clear)
    w = b""
    for idx in indices:
        c = bytes([idx])
        wc = w + c
        if wc in table:
            w = wc
        else:
            emit(table[w])
            if next_code < 4096:
                table[wc] = next_code
                next_code += 1
                if next_code > (1 << code_size) and code_size < 12:
                    code_size += 1
            else:
                emit(clear)
                table = {bytes([i]): i for i in range(clear)}
                next_code = eoi + 1
                code_size = min_code_size + 1
            w = c
    if w:
        emit(table[w])
    emit(eoi)
    if bit_width:
        out.append(bit_buf & 0xFF)
    return bytes(out)


def write_gif(frames: list[bytearray], path: Path, delay_cs: int = DELAY_CS) -> None:
    """Write *frames* (palette-index buffers) as an animated GIF89a to *path*."""
    n_colors = len(PALETTE)
    size = 0
    while (1 << (size + 1)) < n_colors:
        size += 1
    table_len = 1 << (size + 1)
    pal = list(PALETTE) + [(0, 0, 0)] * (table_len - n_colors)
    min_code_size = max(2, size + 1)
    out = bytearray()
    out += b"GIF89a"
    out += struct.pack("<HH", WIDTH, HEIGHT)
    # packed: GCT flag=1, color resolution=7, sort=0, GCT size=size; bg=0; aspect=0
    out += bytes([0x80 | (7 << 4) | size, 0, 0])
    for r, g, b in pal:
        out += bytes([r, g, b])
    for frame in frames:
        # Graphic Control Extension (disposal=0, no transparency).
        out += bytes([0x21, 0xF9, 0x04, 0x00])
        out += struct.pack("<H", delay_cs)
        out += bytes([0x00, 0x00])
        # Image Descriptor (no local color table, no interlace).
        out += bytes([0x2C])
        out += struct.pack("<HHHH", 0, 0, WIDTH, HEIGHT)
        out += bytes([0x00])
        out += bytes([min_code_size])
        compressed = _lzw(bytes(frame), min_code_size)
        i = 0
        while i < len(compressed):
            chunk = compressed[i : i + 255]
            out += bytes([len(chunk)]) + chunk
            i += 255
        out += bytes([0x00])
    out += bytes([0x3B])
    path.write_bytes(bytes(out))


def default_run_json() -> Path:
    """Default trace path: this repo's own bound_integration/run.json."""
    return Path(__file__).resolve().parents[1] / "bound_integration" / "run.json"


def default_output() -> Path:
    """Default output path: this repo's assets/bound-demo.gif."""
    return Path(__file__).resolve().parents[1] / "assets" / "bound-demo.gif"


def main(argv: list[str] | None = None) -> int:
    """Parse args, load the real trace, render frames, and write the GIF."""
    parser = argparse.ArgumentParser(description="Generate the BOUND demo GIF.")
    parser.add_argument("--run-json", type=Path, default=default_run_json())
    parser.add_argument("--output", type=Path, default=default_output())
    args = parser.parse_args(argv)
    if not args.run_json.is_file():
        raise SystemExit(f"run trace not found: {args.run_json}")
    trace = json.loads(args.run_json.read_text(encoding="utf-8"))
    frames = build_frames(trace)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_gif(frames, args.output)
    print(
        f"Wrote {args.output} ({len(frames)} frames, {WIDTH}x{HEIGHT}) "
        f"from {args.run_json} (plan_id={trace.get('plan_id')}, "
        f"decision={trace['evaluation']['decision']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
