# Shared by build_android.sh and build_ios.sh: a private Python environment with Briefcase.
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for candidate in python3.12 python3.13 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)'; then
      PY="$candidate"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 - 3.13 is needed (3.12 recommended): https://www.python.org/downloads/macos/" >&2
  exit 1
fi
echo "Using $("$PY" --version)"

if [ ! -d .venv-mobile ]; then
  "$PY" -m venv .venv-mobile
fi
# shellcheck disable=SC1091
source .venv-mobile/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet "briefcase>=0.4,<0.5"

python make_bundle.py   # pack the current Django code into the app
mkdir -p dist
