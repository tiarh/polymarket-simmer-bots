#!/usr/bin/env python3
"""Overlay a risk/reward box (entry/SL/TP) onto a screenshot.

This is a best-effort visual. We estimate y-mapping from provided chart price range.

Usage:
  python rr_overlay.py --in in.png --out out.png --entry 100 --sl 95 --tp 110 --pmin 90 --pmax 120 --side LONG
"""

from __future__ import annotations

import argparse
from PIL import Image, ImageDraw, ImageFont


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--entry", type=float, required=True)
    ap.add_argument("--sl", type=float, required=True)
    ap.add_argument("--tp", type=float, required=True)
    ap.add_argument("--pmin", type=float, required=True)
    ap.add_argument("--pmax", type=float, required=True)
    ap.add_argument("--side", choices=["LONG", "SHORT"], required=True)
    args = ap.parse_args()

    im = Image.open(args.inp).convert("RGBA")
    w, h = im.size

    # Plot area guess (TradingView typical margins)
    top = int(h * 0.10)
    bottom = int(h * 0.92)
    left = int(w * 0.08)
    right = int(w * 0.92)

    # Put RR box around middle-right area
    box_left = int(w * 0.55)
    box_right = int(w * 0.86)

    pmin = args.pmin
    pmax = args.pmax
    if pmax <= pmin:
        pmax = pmin + 1

    def y(price: float) -> int:
        t = (price - pmin) / (pmax - pmin)
        t = clamp(t, 0.0, 1.0)
        return int(bottom - t * (bottom - top))

    y_entry = y(args.entry)
    y_sl = y(args.sl)
    y_tp = y(args.tp)

    draw = ImageDraw.Draw(im, "RGBA")

    # Risk (red) and reward (teal)
    if args.side == "LONG":
        risk_top, risk_bot = sorted([y_sl, y_entry])
        rew_top, rew_bot = sorted([y_entry, y_tp])
    else:
        # For short: risk is entry->SL above, reward is entry->TP below
        risk_top, risk_bot = sorted([y_entry, y_sl])
        rew_top, rew_bot = sorted([y_tp, y_entry])

    # Fill rectangles
    draw.rectangle([box_left, risk_top, box_right, risk_bot], fill=(255, 0, 0, 70))
    draw.rectangle([box_left, rew_top, box_right, rew_bot], fill=(0, 200, 200, 70))

    # Lines
    line_col = (255, 255, 255, 200)
    draw.line([box_left, y_entry, box_right, y_entry], fill=line_col, width=2)
    draw.line([box_left, y_sl, box_right, y_sl], fill=(255, 80, 80, 220), width=2)
    draw.line([box_left, y_tp, box_right, y_tp], fill=(80, 255, 255, 220), width=2)

    # Labels
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except Exception:
        font = ImageFont.load_default()

    def label(text: str, yy: int, color):
        x = box_right + 8
        draw.text((x, yy - 10), text, fill=color, font=font)

    label(f"TP {args.tp:.2f}", y_tp, (120, 255, 255, 255))
    label(f"Entry {args.entry:.2f}", y_entry, (255, 255, 255, 255))
    label(f"SL {args.sl:.2f}", y_sl, (255, 120, 120, 255))

    im.save(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
