#!/bin/bash
set -e

ENV_FILE=".env.debian.maintainer"
EDITOR="${EDITOR:-nano}"
CHANGELOG="debian/changelog"

DEFAULT_NAME="Uwe Niethammer"
DEFAULT_EMAIL="[uwe@dr-niethammer.de](mailto:uwe@dr-niethammer.de)"

# --- Ensure gbp is installed ---

if ! command -v gbp &> /dev/null; then
sudo apt update
sudo apt install -y git-buildpackage
fi

if ! command -v gbp &> /dev/null; then
echo "❌ Error: git-buildpackage (gbp) is not installed."
exit 1
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

if [ -z "$DEBFULLNAME" ] || [ -z "$DEBEMAIL" ]; then
echo "❌ Missing maintainer info"
exit 1
fi

echo "🔧 Maintainer: $DEBFULLNAME <$DEBEMAIL>"

# --- Branch info ---

ORIG_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "📍 Branch: $ORIG_BRANCH"

# --- Git state ---

LAST_CHANGELOG_COMMIT=$(git log -n1 --pretty=format:%H -- debian/changelog)
NEW_COMMITS=$(git rev-list --count "${LAST_CHANGELOG_COMMIT}..HEAD")

# --- Collect commits ---

RAW_COMMITS=$(git log "${LAST_CHANGELOG_COMMIT}..HEAD" --pretty=format:"%s")

FEATURES=$(echo "$RAW_COMMITS" | grep -Ei '^(feat|feature)' || true)
FIXES=$(echo "$RAW_COMMITS" | grep -Ei '^(fix|bug)' || true)
INTERNAL=$(echo "$RAW_COMMITS" | grep -Ei '^(refactor|chore|cleanup)' || true)
OTHER=$(echo "$RAW_COMMITS" | grep -Ev '^(feat|feature|fix|bug|refactor|chore|cleanup)' || true)

# --- Normalize PR format ---

format_entries() {
sed -E 's/^(.*)#([0-9]+).*/  * PR #\2 \1/'
}

FEATURES=$(echo "$FEATURES" | format_entries | sort -u)
FIXES=$(echo "$FIXES" | format_entries | sort -u)
INTERNAL=$(echo "$INTERNAL" | format_entries | sort -u)
OTHER=$(echo "$OTHER" | sed -E 's/^/  * /' | sort -u)

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

if [[ "$UPSTREAM" =~ ^([0-9.]+)~rc([0-9]+)$ ]]; then
BASE="${BASH_REMATCH[1]}"
RC="${BASH_REMATCH[2]}"
else
BASE="$UPSTREAM"
RC=""
fi

echo "Current: $LAST_VERSION"

# --- Ask increment ---

echo "[b] major  [m] minor  [p] patch  [r] revision"
read -p "Choice [r]: " choice

IFS='.' read -r MAJOR MINOR PATCH <<< "$BASE"

case "$choice" in
[bB]) MAJOR=$((MAJOR+1)); MINOR=0; PATCH=0; NEW_REV=1 ;;
[mM]) MINOR=$((MINOR+1)); PATCH=0; NEW_REV=1 ;;
[pP]) PATCH=$((PATCH+1)); NEW_REV=1 ;;
*) NEW_REV=$((REV+1)) ;;
esac

NEW_BASE="${MAJOR}.${MINOR}.${PATCH}"
NEW_VERSION="${NEW_BASE}-${NEW_REV}"

# --- Distribution ---

echo "[u] unstable [n] next [e] experimental [c] rc [r] release [x] UNRELEASED"
read -p "Choice [x]: " dist_choice

case "$dist_choice" in
[uU]) DIST="unstable"; SETUP_SUFFIX=".dev${NEW_REV}" ;;
[nN]) DIST="next"; SETUP_SUFFIX=".dev${NEW_REV}" ;;
[eE]) DIST="experimental"; SETUP_SUFFIX=".dev${NEW_REV}" ;;
[cC])
RC_NUM=$((RC+1))
DIST="rc"
SETUP_SUFFIX="rc${RC_NUM}"
NEW_VERSION="${NEW_BASE}~rc${RC_NUM}-${NEW_REV}"
;;
[rR]) DIST="release"; SETUP_SUFFIX="" ;;
*) DIST="UNRELEASED"; SETUP_SUFFIX=".post${NEW_REV}" ;;
esac

echo "→ Version: $NEW_VERSION ($DIST)"
read -p "OK? [Y/n] " c
[[ "$c" =~ ^[Nn]$ ]] && exit 1

# --- gbp workaround: switch to master ---

echo "🛠 Preparing gbp compatibility..."

if git show-ref --verify --quiet refs/heads/master; then
git checkout -q master
else
git checkout -q -b master
fi

git reset --hard "$ORIG_BRANCH"

# --- Generate changelog ---

echo "📝 Generating changelog..."

gbp dch --auto 
--ignore-regex='#[0-9]+' 
--new-version="$NEW_VERSION" 
--distribution="$DIST"

# --- Switch back ---

git checkout -q "$ORIG_BRANCH"

# --- Inject structured sections ---

echo "🔗 Injecting structured changelog..."

awk -v f="$FEATURES" -v x="$FIXES" -v i="$INTERNAL" -v o="$OTHER" '
function print_block(title, data) {
if (length(data) > 0) {
print "  [" title "]"
n = split(data, lines, "\n")
for (j = 1; j <= n; j++) {
if (length(lines[j]) > 0) print lines[j]
}
print ""
}
}

NR==1 { print; next }
NR==2 {
print_block("Features", f)
print_block("Fixes", x)
print_block("Internal", i)
print_block("Other", o)
}
{ print }
' "$CHANGELOG" > "${CHANGELOG}.tmp" && mv "${CHANGELOG}.tmp" "$CHANGELOG"

# --- Optional edit ---

read -p "Edit changelog? [e/N] " e
[[ "$e" =~ ^[eE]$ ]] && $EDITOR "$CHANGELOG"

# --- Update files ---

sed -i "1s/^# Onboard .*/# Onboard ${NEW_VERSION}/" README.md
sed -i "s/version = '[^']*'/version = '${NEW_BASE}${SETUP_SUFFIX}'/" setup.py

# --- Commit ---

git add "$CHANGELOG" README.md setup.py

if [ "$BASE" != "$NEW_BASE" ]; then
git commit -m "Update version: $NEW_VERSION"
else
git commit -m "Update changelog revision: $NEW_VERSION"
fi

# --- Push ---

git push

echo "✅ Done."

