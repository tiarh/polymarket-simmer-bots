#!/usr/bin/env bash
set -euo pipefail

OUT=$(/root/weather-env/bin/python /root/.openclaw/workspace/scripts/btc15m_arb_report.py || true)

if [ -n "${OUT}" ]; then
  openclaw message send --channel telegram --target 1089213658 --message "${OUT}"
fi
