#!/bin/bash
set -euo pipefail

ENV_FILE=".env.debian.maintainer"
CHANGELOG="debian/changelog"

DEFAULT_NAME="Uwe Niethammer"
DEFAULT_EMAIL="68241100+dr-ni@users.noreply.github.com"

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

# Sicherheitscheck: keine Markdown-Links in der E-Mail
if [[ "$DEBEMAIL" == *"["* || "$DEBEMAIL" == *"("* ]]; then
    echo "❌ DEBEMAIL enthält ungültige Zeichen (Markdown-Link?): $DEBEMAIL"
    echo "   Bitte $ENV_FILE korrigieren."
    exit 1
fi

echo "🔧 Maintainer: $DEBFULLNAME <$DEBEMAIL>"

# --- Ensure main branch ---

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "📍 Branch: $BRANCH"
[ "$BRANCH" = "main" ] || { echo "❌ Run on main"; exit 1; }

# --- Determine commit range since last changelog commit ---

LAST_CHANGELOG_COMMIT=$(git log -n1 --pretty=format:%H -- "$CHANGELOG" 2>/dev/null || true)

if [ -z "$LAST_CHANGELOG_COMMIT" ]; then
    RAW_COMMITS=$(git log --pretty=format:"%s" || true)
else
    RAW_COMMITS=$(git log "${LAST_CHANGELOG_COMMIT}..HEAD" --pretty=format:"%s" || true)
fi

# --- Filter out noise ---
# Merge-Commits, Versionsbumps und leere Zeilen entfernen

clean_commits() {
    echo "$1" \
        | grep -v '^\s*$' \
        | grep -Eiv '^merge (pull request|branch)' \
        | grep -Eiv '^update (version|changelog)' \
        | sort -u
}

RAW_COMMITS=$(clean_commits "$RAW_COMMITS")

# --- Classify commits ---

FEATURES=$(echo "$RAW_COMMITS" | grep -Ei '^(feat|feature)' || true)
FIXES=$(echo "$RAW_COMMITS"    | grep -Ei '^(fix|bug)'      || true)
INTERNAL=$(echo "$RAW_COMMITS" | grep -Ei '^(refactor|chore|cleanup|revert)' || true)
OTHER=$(echo "$RAW_COMMITS"    | grep -Eiv '^(feat|feature|fix|bug|refactor|chore|cleanup|revert)' || true)

# --- Normalize: PR-Referenz anhängen wenn vorhanden, aber kein doppelter Text ---
# Aus "Fix foo (#42)" wird "Fix foo (PR #42)" - kein separater PR-Eintrag

normalize_entry() {
    sed -E 's/\(#([0-9]+)\)/(PR #\1)/'
}

FEATURES=$(echo "$FEATURES" | normalize_entry | sort -u)
FIXES=$(echo "$FIXES"       | normalize_entry | sort -u)
INTERNAL=$(echo "$INTERNAL" | normalize_entry | sort -u)
OTHER=$(echo "$OTHER"       | normalize_entry | sort -u)

# --- Extract existing entries from current changelog block (robust) ---
# Liest alles zwischen Zeile 1 (Header) und der Signaturzeile " -- "

EXISTING=$(awk '
    /^ -- / { exit }
    NR > 1  { sub(/^[[:space:]]*\*[[:space:]]*/, ""); print }
' "$CHANGELOG" 2>/dev/null | grep -v '^\s*$' | sort -u || true)

# --- Dedup: nur wirklich neue Einträge behalten ---

filter_new() {
    local entries="$1"
    [ -z "$entries" ] && return
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        # Prüfe ob diese Zeile (oder sehr ähnlich) schon im EXISTING vorkommt
        if ! echo "$EXISTING" | grep -qF "$line"; then
            echo "$line"
        fi
    done <<< "$entries"
}

FEATURES=$(filter_new "$FEATURES")
FIXES=$(filter_new "$FIXES")
INTERNAL=$(filter_new "$INTERNAL")
OTHER=$(filter_new "$OTHER")

# --- Exit if nothing new ---

if [ -z "${FEATURES}${FIXES}${INTERNAL}${OTHER}" ]; then
    echo "✔ Keine neuen Changelog-Einträge."
    exit 0
fi

# --- Vorschau anzeigen ---

echo ""
echo "📋 Neue Einträge:"
[ -n "$FEATURES" ] && echo "$FEATURES" | sed 's/^/  [Feature] /'
[ -n "$FIXES" ]    && echo "$FIXES"    | sed 's/^/  [Fix]     /'
[ -n "$INTERNAL" ] && echo "$INTERNAL" | sed 's/^/  [Intern]  /'
[ -n "$OTHER" ]    && echo "$OTHER"    | sed 's/^/  [Other]   /'
echo ""

# --- Version parsing ---

LAST_VERSION=$(dpkg-parsechangelog -S Version)
UPSTREAM="${LAST_VERSION%-*}"
REV="${LAST_VERSION##*-}"

IFS='.' read -r MAJOR MINOR PATCH <<< "$UPSTREAM"

echo "Aktuelle Version: $LAST_VERSION"
echo "[b] major  [m] minor  [p] patch  [r] revision (Standard)  [c] release current"
read -p "Wahl [r]: " choice

if [[ "$choice" =~ ^[cC]$ ]]; then
    DIST="release"
    NEW_VERSION="$LAST_VERSION"
    NEW_BASE="$UPSTREAM"
    echo "→ Releasing current version: $NEW_VERSION"
    read -p "OK? [Y/n] " c
    [[ "$c" =~ ^[Nn]$ ]] && exit 1

    SCRIPT_DIR="$(dirname "$0")"
    echo "🔨 Building Debian packages..."
    bash "$SCRIPT_DIR/build_debs.sh"

    TAG="v$NEW_VERSION"
    echo "🏷  Tagging $TAG"
    git tag -a "$TAG" -m "Release $NEW_VERSION" 2>/dev/null || echo "Tag already exists"
    git push origin "$TAG" 2>/dev/null || echo "Tag already pushed"

    if command -v gh >/dev/null; then
        TARBALL_PATH="$SCRIPT_DIR/build/debs/onboard_${NEW_VERSION}.orig.tar.gz"
        if [ -f "$TARBALL_PATH" ]; then
            gpg --batch --yes --detach-sign --armor "$TARBALL_PATH"
            gh release create "$TAG"                 --title "Onboard $NEW_VERSION"                 --notes "Release $NEW_VERSION"                 "$TARBALL_PATH"                 "${TARBALL_PATH}.asc" || true
        else
            gh release create "$TAG"                 --title "Onboard $NEW_VERSION"                 --notes "Release $NEW_VERSION" || true
        fi
    fi
    echo "✅ Fertig."
    exit 0
fi

case "$choice" in
    [bB]) MAJOR=$((MAJOR+1)); MINOR=0; PATCH=0; NEW_REV=1 ;;
    [mM]) MINOR=$((MINOR+1)); PATCH=0; NEW_REV=1 ;;
    [pP]) PATCH=$((PATCH+1)); NEW_REV=1 ;;
    *)    NEW_REV=$((REV+1)) ;;
esac

NEW_BASE="${MAJOR}.${MINOR}.${PATCH}"
NEW_VERSION="${NEW_BASE}-${NEW_REV}"

# --- Distribution ---

echo "[u] unstable  [n] next  [e] experimental  [r] release  [x] UNRELEASED (Standard)"
read -p "Wahl [x]: " dist_choice

SETUP_SUFFIX=""
case "$dist_choice" in
    [uU]) DIST="unstable";     SETUP_SUFFIX=".dev${NEW_REV}" ;;
    [nN]) DIST="next";         SETUP_SUFFIX=".dev${NEW_REV}" ;;
    [eE]) DIST="experimental"; SETUP_SUFFIX=".dev${NEW_REV}" ;;
    [rR]) DIST="release";      SETUP_SUFFIX="" ;;
    *)    DIST="UNRELEASED";   SETUP_SUFFIX=".post${NEW_REV}" ;;
esac

echo "→ Version: $NEW_VERSION ($DIST)"
read -p "OK? [Y/n] " c
[[ "$c" =~ ^[Nn]$ ]] && exit 1

# --- Changelog-Eintrag per dch anlegen ---

DCH_DIST=$([ "$DIST" = "release" ] && echo "unstable" || echo "$DIST")

dch --newversion "$NEW_VERSION" \
    --distribution "$DCH_DIST" \
    --force-distribution \
    "dummy"

# --- Strukturierten Inhalt aufbauen ---

TMP=$(mktemp)

add_block() {
    local title="$1"
    local content="$2"
    [ -z "$content" ] && return
    echo "  * [$title]" >> "$TMP"
    while IFS= read -r line; do
        [ -n "$line" ] && echo "  * $line" >> "$TMP"
    done <<< "$content"
    echo "" >> "$TMP"
}

add_block "Features" "$FEATURES"
add_block "Fixes"    "$FIXES"
add_block "Internal" "$INTERNAL"
add_block "Other"    "$OTHER"

# --- Dummy-Eintrag im Changelog ersetzen ---
# dch schreibt "  * dummy" - das ersetzen wir durch unsere Blöcke

python3 - "$CHANGELOG" "$TMP" <<'PYEOF'
import sys

changelog_path = sys.argv[1]
entries_path   = sys.argv[2]

with open(changelog_path, "r") as f:
    lines = f.readlines()

with open(entries_path, "r") as f:
    new_entries = f.read()

# Ersten Block finden und dummy ersetzen
out = []
replaced = False
i = 0
while i < len(lines):
    line = lines[i]
    if not replaced and line.strip() == "* dummy":
        out.append(new_entries)
        replaced = True
    else:
        out.append(line)
    i += 1

with open(changelog_path, "w") as f:
    f.writelines(out)
PYEOF

rm "$TMP"

# --- Changelog validieren ---

dpkg-parsechangelog >/dev/null
echo "✅ Changelog validiert."

# --- setup.py Version aktualisieren ---

NEW_PY_VERSION="${NEW_BASE}${SETUP_SUFFIX}"
echo "🐍 Python-Version → $NEW_PY_VERSION"

sed -i -E "s/version *= *'[^']*'/version = '${NEW_PY_VERSION}'/" setup.py

# --- Versionskonsistenz prüfen ---

PY_VERSION=$(grep -E "version *= *'" setup.py | sed -E "s/.*'([^']+)'.*/\1/")

if [[ "$DIST" == "release" && "$PY_VERSION" == *".dev"* ]]; then
    echo "❌ Ungültige Release-Version (.dev nicht erlaubt)"
    exit 1
fi

# --- Commit & Push ---

git add "$CHANGELOG" setup.py
git commit -m "Update version: $NEW_VERSION"
git push

# --- Tag & GitHub Release (nur bei release) ---

if [ "$DIST" = "release" ]; then
    TAG="v$NEW_VERSION"
    echo "🏷  Tagging $TAG"

    git tag -a "$TAG" -m "Release $NEW_VERSION"
    git push origin "$TAG"

    if command -v gh >/dev/null; then
        echo "🚀 GitHub Release erstellen..."

        NOTES=$(awk '
            /^ -- / { exit }
            NR > 1  { print }
        ' "$CHANGELOG")

        # --- Tarball erstellen und signieren ---
        TARBALL="onboard_${NEW_VERSION}.orig.tar.gz"
        TARBALL_PATH="$(dirname "$0")/build/debs/${TARBALL}"

        if [ -f "$TARBALL_PATH" ]; then
            echo "🔏 Signing tarball..."
            gpg --batch --yes --detach-sign --armor "$TARBALL_PATH"
            ASC_PATH="${TARBALL_PATH}.asc"
            echo "✅ Tarball signed: $ASC_PATH"

            gh release create "$TAG" \
                --title "Onboard $NEW_VERSION" \
                --notes "$NOTES" \
                "$TARBALL_PATH" \
                "$ASC_PATH"
        else
            echo "⚠️  Tarball nicht gefunden: $TARBALL_PATH"
            gh release create "$TAG" \
                --title "Onboard $NEW_VERSION" \
                --notes "$NOTES"
        fi
    fi
fi

echo "✅ Fertig."
