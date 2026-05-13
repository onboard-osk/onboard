#!/bin/bash
set -euo pipefail

ENV_FILE=".env.debian.maintainer"
EDITOR="${EDITOR:-nano}"
CHANGELOG="debian/changelog"

DEFAULT_NAME="Uwe Niethammer"
DEFAULT_EMAIL="[uwe@dr-niethammer.de](mailto:uwe@dr-niethammer.de)"

# --- Cleanup stale dch backup ---

[ -f debian/changelog.dch ] && rm debian/changelog.dch

# --- Ensure tools ---

command -v dch >/dev/null || sudo apt install -y devscripts
command -v gh >/dev/null || echo "⚠️ gh not installed (no GitHub release)"

# --- Create .env if missing ---

if [ ! -f "$ENV_FILE" ]; then
cat > "$ENV_FILE" <<EOF
DEBFULLNAME="$DEFAULT_NAME"
DEBEMAIL="$DEFAULT_EMAIL"
EOF
fi

# --- Load env ---

source "$ENV_FILE"
export DEBFULLNAME DEBEMAIL

echo "🔧 Maintainer: $DEBFULLNAME <$DEBEMAIL>"

# --- Ensure main branch ---

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "📍 Branch: $BRANCH"
[ "$BRANCH" = "main" ] || { echo "❌ Run on main"; exit 1; }

# --- Determine range ---

LAST_CHANGELOG_COMMIT=$(git log -n1 --pretty=format:%H -- "$CHANGELOG" || true)
RAW_COMMITS=$(git log "${LAST_CHANGELOG_COMMIT}..HEAD" --pretty=format:"%s" || true)

# --- Classify commits ---

FEATURES=$(echo "$RAW_COMMITS" | grep -Ei '^(feat|feature)' || true)
FIXES=$(echo "$RAW_COMMITS" | grep -Ei '^(fix|bug)' || true)
INTERNAL=$(echo "$RAW_COMMITS" | grep -Ei '^(refactor|chore|cleanup)' || true)
OTHER=$(echo "$RAW_COMMITS" | grep -Ev '^(feat|feature|fix|bug|refactor|chore|cleanup)' || true)

# --- Normalize PR format ---

format_entries() {
sed -E 's/^(.*)#([0-9]+).*/PR #\2 \1/'
}

FEATURES=$(echo "$FEATURES" | format_entries | sort -u)
FIXES=$(echo "$FIXES" | format_entries | sort -u)
INTERNAL=$(echo "$INTERNAL" | format_entries | sort -u)
OTHER=$(echo "$OTHER" | sort -u)

# --- Extract existing entries from latest changelog block ---

EXISTING=$(awk '
NR==1{next}
NR==2{flag=1; next}
flag && /^ -- /{exit}
flag {gsub(/^  * /,""); print}
' "$CHANGELOG" 2>/dev/null | sort -u)

# --- Dedup function ---

filter_new() {
comm -23 <(echo "$1" | sort -u) <(echo "$EXISTING")
}

FEATURES=$(filter_new "$FEATURES")
FIXES=$(filter_new "$FIXES")
INTERNAL=$(filter_new "$INTERNAL")
OTHER=$(filter_new "$OTHER")

# --- Exit if nothing new ---

if [ -z "${FEATURES}${FIXES}${INTERNAL}${OTHER}" ]; then
echo "✔ No new changelog entries"
exit 0
fi

# --- Version parsing ---

LAST_VERSION=$(dpkg-parsechangelog -S Version)
UPSTREAM="${LAST_VERSION%-*}"
REV="${LAST_VERSION##*-}"

IFS='.' read -r MAJOR MINOR PATCH <<< "$UPSTREAM"

echo "Current: $LAST_VERSION"
echo "[b] major  [m] minor  [p] patch  [r] revision"
read -p "Choice [r]: " choice

case "$choice" in
[bB]) MAJOR=$((MAJOR+1)); MINOR=0; PATCH=0; NEW_REV=1 ;;
[mM]) MINOR=$((MINOR+1)); PATCH=0; NEW_REV=1 ;;
[pP]) PATCH=$((PATCH+1)); NEW_REV=1 ;;
*) NEW_REV=$((REV+1)) ;;
esac

NEW_BASE="${MAJOR}.${MINOR}.${PATCH}"
NEW_VERSION="${NEW_BASE}-${NEW_REV}"

# --- Distribution ---

echo "[u] unstable [n] next [e] experimental [r] release [x] UNRELEASED"
read -p "Choice [x]: " dist_choice

SETUP_SUFFIX=""
case "$dist_choice" in
[uU]) DIST="unstable"; SETUP_SUFFIX=".dev${NEW_REV}" ;;
[nN]) DIST="next"; SETUP_SUFFIX=".dev${NEW_REV}" ;;
[eE]) DIST="experimental"; SETUP_SUFFIX=".dev${NEW_REV}" ;;
[rR]) DIST="release"; SETUP_SUFFIX="" ;;
*) DIST="UNRELEASED"; SETUP_SUFFIX=".post${NEW_REV}" ;;
esac

echo "→ Version: $NEW_VERSION ($DIST)"
read -p "OK? [Y/n] " c
[[ "$c" =~ ^[Nn]$ ]] && exit 1

# --- Create changelog entry ---

DCH_DIST=$([ "$DIST" = "release" ] && echo "unstable" || echo "$DIST")

dch --newversion "$NEW_VERSION" \
--distribution "$DCH_DIST" \
--force-distribution ""

# --- Build structured entries ---

TMP=$(mktemp)

add_block() {
TITLE="$1"
CONTENT="$2"
if [ -n "$CONTENT" ]; then
echo "  * [$TITLE]" >> "$TMP"
echo "$CONTENT" | while read -r l; do
[ -n "$l" ] && echo "  * $l" >> "$TMP"
done
echo "" >> "$TMP"
fi
}

add_block "Features" "$FEATURES"
add_block "Fixes" "$FIXES"
add_block "Internal" "$INTERNAL"
add_block "Other" "$OTHER"

# --- Inject after header ---

awk -v file="$TMP" '
NR==1{print;next}
NR==2{while((getline l<file)>0)print l}
{print}
' "$CHANGELOG" > "$CHANGELOG.tmp" && mv "$CHANGELOG.tmp" "$CHANGELOG"

rm "$TMP"

# --- Validate changelog ---

dpkg-parsechangelog >/dev/null

# --- Update setup.py version ---

NEW_PY_VERSION="${NEW_BASE}${SETUP_SUFFIX}"
echo "🐍 Python version → $NEW_PY_VERSION"

sed -i -E "s/version *= *'[^']*'/version = '${NEW_PY_VERSION}'/" setup.py

# --- Validate version consistency ---

PY_VERSION=$(grep -E "version *= *'" setup.py | sed -E "s/.*'([^']+)'.*/\1/")

if [[ "$DIST" == "release" && "$PY_VERSION" == *".dev"* ]]; then
echo "❌ Invalid release version (.dev not allowed)"
exit 1
fi

# --- Commit ---

git add "$CHANGELOG" README.md setup.py
git commit -m "Update version: $NEW_VERSION"
git push

# --- Tag & GitHub Release ---

if [ "$DIST" = "release" ]; then
TAG="v$NEW_BASE"
echo "🏷 Tagging $TAG"

git tag -a "$TAG" -m "Release $NEW_VERSION"
git push origin "$TAG"

if command -v gh >/dev/null; then
echo "🚀 Creating GitHub release..."

```
NOTES=$(awk '
NR==1{next}
NR==2{f=1;next}
f && /^ -- /{exit}
f{print}
' "$CHANGELOG")

gh release create "$TAG" \
  --title "Onboard $NEW_BASE" \
  --notes "$NOTES"
```

fi
fi

echo "✅ Done (no duplicates, no gbp, fully automated)."

