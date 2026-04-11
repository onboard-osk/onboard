#!/bin/bash
set -e

ENV_FILE=".env.debian.maintainer"
EDITOR="${EDITOR:-nano}"
CHANGELOG="debian/changelog"

DEFAULT_NAME="Uwe Niethammer"
DEFAULT_EMAIL="[uwe@dr-niethammer.de](mailto:uwe@dr-niethammer.de)"

# --- Ensure dch exists ---

if ! command -v dch &> /dev/null; then
sudo apt update
sudo apt install -y devscripts
fi

# --- Create .env if missing ---

if [ ! -f "$ENV_FILE" ]; then
echo "⚠️ Creating $ENV_FILE ..."
cat > "$ENV_FILE" <<EOF
DEBFULLNAME="$DEFAULT_NAME"
DEBEMAIL="$DEFAULT_EMAIL"
EOF
fi

# --- Load env ---

source "$ENV_FILE"
export DEBFULLNAME
export DEBEMAIL

echo "🔧 Maintainer: $DEBFULLNAME <$DEBEMAIL>"

# --- Ensure on main ---

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "📍 Branch: $CURRENT_BRANCH"

if [ "$CURRENT_BRANCH" != "main" ]; then
echo "❌ Please run on main branch"
exit 1
fi

# --- Git state ---

LAST_CHANGELOG_COMMIT=$(git log -n1 --pretty=format:%H -- debian/changelog)
NEW_COMMITS=$(git rev-list --count "${LAST_CHANGELOG_COMMIT}..HEAD")

# --- Collect commits ---

RAW_COMMITS=$(git log "${LAST_CHANGELOG_COMMIT}..HEAD" --pretty=format:"%s")

FEATURES=$(echo "$RAW_COMMITS" | grep -Ei '^(feat|feature)' || true)
FIXES=$(echo "$RAW_COMMITS" | grep -Ei '^(fix|bug)' || true)
INTERNAL=$(echo "$RAW_COMMITS" | grep -Ei '^(refactor|chore|cleanup)' || true)
OTHER=$(echo "$RAW_COMMITS" | grep -Ev '^(feat|feature|fix|bug|refactor|chore|cleanup)' || true)

# --- Normalize PR format (always valid Debian style) ---

format_entries() {
sed -E 's/^(.*)#([0-9]+).*/PR #\2 \1/'
}

FEATURES=$(echo "$FEATURES" | format_entries | sort -u)
FIXES=$(echo "$FIXES" | format_entries | sort -u)
INTERNAL=$(echo "$INTERNAL" | format_entries | sort -u)
OTHER=$(echo "$OTHER" | sort -u)

# --- Warn if no commits ---

if [ "$NEW_COMMITS" -eq 0 ]; then
echo "⚠️ No new commits since last changelog."
read -p "Continue anyway? [y/N] " r
[[ "$r" =~ ^[yY]$ ]] || exit 0
fi

# --- Version parsing ---

LAST_VERSION=$(dpkg-parsechangelog -S Version)
UPSTREAM="${LAST_VERSION%-*}"
REV="${LAST_VERSION##*-}"

IFS='.' read -r MAJOR MINOR PATCH <<< "$UPSTREAM"

echo "Current: $LAST_VERSION"

# --- Ask increment ---

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

# --- Create new changelog entry ---

echo "📝 Creating changelog..."

dch --newversion "$NEW_VERSION" --distribution "$DIST" ""

# --- Build structured entries (valid format) ---

TMP_ENTRIES=$(mktemp)

add_block() {
TITLE="$1"
CONTENT="$2"
if [ -n "$CONTENT" ]; then
echo "  * [$TITLE]" >> "$TMP_ENTRIES"
echo "$CONTENT" | while read -r line; do
[ -n "$line" ] && echo "  * $line" >> "$TMP_ENTRIES"
done
echo "" >> "$TMP_ENTRIES"
fi
}

add_block "Features" "$FEATURES"
add_block "Fixes" "$FIXES"
add_block "Internal" "$INTERNAL"
add_block "Other" "$OTHER"

# --- Inject after header ---

awk -v file="$TMP_ENTRIES" '
NR==1 { print; next }
NR==2 {
while ((getline line < file) > 0) print line
}
{ print }
' "$CHANGELOG" > "${CHANGELOG}.tmp" && mv "${CHANGELOG}.tmp" "$CHANGELOG"

rm "$TMP_ENTRIES"

# --- Validate format ---

echo "🔍 Validating changelog..."
dpkg-parsechangelog > /dev/null

# --- Optional edit ---

read -p "Edit changelog? [e/N] " e
[[ "$e" =~ ^[eE]$ ]] && $EDITOR "$CHANGELOG"

# --- Update files ---

sed -i "1s/^# Onboard .*/# Onboard ${NEW_VERSION}/" README.md
sed -i "s/version = '[^']*'/version = '${NEW_BASE}${SETUP_SUFFIX}'/" setup.py

# --- Commit ---

git add "$CHANGELOG" README.md setup.py

if [ "$UPSTREAM" != "$NEW_BASE" ]; then
git commit -m "Update version: $NEW_VERSION"
else
git commit -m "Update changelog revision: $NEW_VERSION"
fi

git push

echo "✅ Done (clean Debian changelog, no gbp)."

