#!/usr/bin/env bash
# AR-Clipper — مشغّل لينكس/ماك (نظير arc.bat على ويندوز)
#   ./arc.sh doctor
#   ./arc.sh clip video.mp4 -s 0 -e 30 -L personal_test
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
    echo "[خطأ] البيئة الافتراضية غير موجودة." >&2
    echo "  شغّل أولاً:" >&2
    echo "      python3 -m venv .venv" >&2
    echo "      .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

exec "$PY" -m cli.main "$@"
