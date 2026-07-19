#!/usr/bin/env bash
# Build the Chrome Web Store release ZIP for the InfoDiet extension.
#
# Allowlist packaging: ONLY the runtime files ship (manifest at the ZIP root, icons included);
# tests, README, and this script never can — anything not listed below is not in the bundle.
# Output: extension/dist/infodiet-extension-<version>.zip (dist/ is gitignored).
#
# Usage: bash extension/package.sh
set -euo pipefail
cd "$(dirname "$0")"

RUNTIME_FILES=(
  manifest.json
  background.js
  common.js
  content.js
  options.html
  options.js
  icons/icon16.png
  icons/icon32.png
  icons/icon48.png
  icons/icon128.png
)

for f in "${RUNTIME_FILES[@]}"; do
  [[ -f "$f" ]] || { echo "missing runtime file: $f" >&2; exit 1; }
done

VERSION=$(python3 -c "import json; print(json.load(open('manifest.json'))['version'])")
OUT="dist/infodiet-extension-${VERSION}.zip"
mkdir -p dist
rm -f "$OUT"
zip -q -X "$OUT" "${RUNTIME_FILES[@]}"

# Verify the bundle is exactly the allowlist — no more, no less — with manifest at the root.
python3 - "$OUT" "${RUNTIME_FILES[@]}" <<'EOF'
import sys, zipfile
out, expected = sys.argv[1], sorted(sys.argv[2:])
names = sorted(zipfile.ZipFile(out).namelist())
assert names == expected, f"bundle mismatch:\n  got      {names}\n  expected {expected}"
assert "manifest.json" in names, "manifest.json not at ZIP root"
print(f"{out}: {len(names)} files, contents verified")
EOF

echo "release bundle ready: extension/$OUT"
