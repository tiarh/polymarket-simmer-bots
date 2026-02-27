#!/usr/bin/env python3
"""Take a screenshot of a TradingView chart URL.

Usage:
  /root/weather-env/bin/python scripts/tv_snapshot.py --url '<tv url>' --out '/tmp/tv.png'

Notes:
- TradingView is JS-heavy and may show cookie/login popups.
- We best-effort close common dialogs.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--timeout", type=int, default=30000)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        ctx = browser.new_context(viewport={"width": args.width, "height": args.height})
        page = ctx.new_page()

        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout)

        # Give time for chart canvas to render.
        page.wait_for_timeout(4000)

        # Try dismissing common popups (best effort)
        for sel in [
            "button:has-text('Accept all')",
            "button:has-text('I understand')",
            "button:has-text('Got it')",
            "button[aria-label='Close']",
            "div[role='dialog'] button:has-text('Close')",
        ]:
            try:
                page.locator(sel).first.click(timeout=1500)
                page.wait_for_timeout(500)
            except Exception:
                pass

        # wait for canvas element likely present
        try:
            page.wait_for_selector("canvas", timeout=5000)
        except PWTimeout:
            pass

        page.screenshot(path=str(out), full_page=True)
        ctx.close()
        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
