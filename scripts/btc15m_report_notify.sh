#!/usr/bin/env bash
set -euo pipefail

OUT=$(/root/weather-env/bin/python /root/.openclaw/workspace/scripts/btc15m_arb_report.py || true)

if [ -n "${OUT}" ]; then
  : "${TELEGRAM_TARGET:?Set TELEGRAM_TARGET (chat id) to receive reports}"
  openclaw message send --channel telegram --target "${TELEGRAM_TARGET}" --message "${OUT}"
fi
