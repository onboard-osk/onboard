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

# --- Determine commit range since last tag ---

LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || true)

if [ -z "$LAST_TAG" ]; then
    RAW_COMMITS=$(git log --pretty=format:"%s" || true)
else
    RAW_COMMITS=$(git log "${LAST_TAG}..HEAD" --pretty=format:"%s" || true)
fi

# --- Filter out noise ---
# Merge-Commits, Versionsbumps und leere Zeilen entfernen

clean_commits() {
    echo "$1" \
        | grep -v '^\s*$' \
        | grep -Eiv '^merge (pull request|branch)' \
        | grep -Eiv '^update (version|changelog)' \
        | sort -u \
        || true
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

# --- Release current version (used by both paths below) ---

release_current() {
    local NEW_VERSION="$LAST_VERSION"
    local NEW_BASE="$UPSTREAM"
    echo "→ Releasing current version: $NEW_VERSION"

    if [ -n "${FEATURES}${FIXES}${INTERNAL}${OTHER}" ]; then
        echo ""
        echo "⚠️  WARNUNG: Es gibt klassifizierte, aber NICHT ins Changelog"
        echo "   geschriebene Commits (siehe Vorschau oben). release_current()"
        echo "   schreibt KEINEN changelog-Eintrag, sondern released nur die"
        echo "   Version, die aktuell oben in debian/changelog steht."
        echo "   Falls diese Commits dokumentiert werden sollen: abbrechen (n)"
        echo "   und stattdessen [w]/[b]/[m]/[p]/[r] wählen."
        echo ""
    fi

    read -p "OK? [Y/n] " c
    [[ "$c" =~ ^[Nn]$ ]] && exit 1

    local SCRIPT_DIR
    SCRIPT_DIR="$(dirname "$0")"

    if [ -x "$SCRIPT_DIR/refresh-translations.sh" ]; then
        echo "🌐 Refreshing translations..."
        bash "$SCRIPT_DIR/refresh-translations.sh"
    else
        echo "⚠️  refresh-translations.sh not found or not executable, skipping."
    fi

    echo "🐍 Python-Version → $NEW_VERSION"
    sed -i -E "s/version *= *'[^']*'/version = '${NEW_VERSION}'/" setup.py
    sed -i "s/^# Onboard .*/# Onboard ${NEW_VERSION}/" README.md
    if ! git diff --quiet -- setup.py README.md; then
        git add setup.py README.md
        git commit -m "Update version: $NEW_VERSION"
        git push
    fi

    echo "🔨 Building Debian packages..."
    bash "$SCRIPT_DIR/build_debs.sh"

    local TAG="v$NEW_VERSION"
    echo "🏷  Tagging $TAG"
    git tag -s "$TAG" -m "Release $NEW_VERSION" 2>/dev/null || echo "Tag already exists"
    git push origin "$TAG" 2>/dev/null || echo "Tag already pushed"

    if command -v gh >/dev/null; then
        local TARBALL_PATH="$SCRIPT_DIR/build/debs/onboard_${NEW_BASE}.orig.tar.gz"
        local CHANGELOG_NOTES
        CHANGELOG_NOTES=$(awk "/^onboard \($NEW_VERSION\)/,/^ -- /" "$CHANGELOG" | grep "^\s*\*" | sed "s/^\s*\* //")
        if [ -f "$TARBALL_PATH" ]; then
            gpg --batch --yes --detach-sign --armor "$TARBALL_PATH"
            if gh release view "$TAG" >/dev/null 2>&1; then
                gh release upload "$TAG" "$TARBALL_PATH" "${TARBALL_PATH}.asc" --clobber
                gh release edit "$TAG" --notes "$CHANGELOG_NOTES"
            else
                gh release create "$TAG" --title "$NEW_VERSION" --notes "$CHANGELOG_NOTES" "$TARBALL_PATH" "${TARBALL_PATH}.asc"
            fi
        else
            if gh release view "$TAG" >/dev/null 2>&1; then
                gh release edit "$TAG" --notes "$CHANGELOG_NOTES"
            else
                gh release create "$TAG" --title "$NEW_VERSION" --notes "$CHANGELOG_NOTES" || true
            fi
        fi
    fi
    echo "✅ Fertig."
}

# --- Version parsing (needed even if nothing new, for the fallback release path) ---

LAST_VERSION=$(dpkg-parsechangelog -S Version)
UPSTREAM="${LAST_VERSION%-*}"
REV="${LAST_VERSION##*-}"

IFS='.' read -r MAJOR MINOR PATCH <<< "$UPSTREAM"

# --- Exit if nothing new (but still allow a re-release or a plain version bump) ---

if [ -z "${FEATURES}${FIXES}${INTERNAL}${OTHER}" ]; then
    echo "✔ Keine neuen Changelog-Einträge."
    read -p "[c] aktuelle Version erneut releasen  [w] normal weiter (Revision/Version wählen)  [N] abbrechen: " nothing_new_choice
    case "$nothing_new_choice" in
        [cC])
            release_current
            exit 0
            ;;
        [wW])
            OTHER="No user-visible changes."
            ;;
        *)
            exit 0
            ;;
    esac
fi

# --- Vorschau anzeigen ---

echo ""
echo "📋 Neue Einträge:"
[ -n "$FEATURES" ] && echo "$FEATURES" | sed 's/^/  [Feature] /'
[ -n "$FIXES" ]    && echo "$FIXES"    | sed 's/^/  [Fix]     /'
[ -n "$INTERNAL" ] && echo "$INTERNAL" | sed 's/^/  [Intern]  /'
[ -n "$OTHER" ]    && echo "$OTHER"    | sed 's/^/  [Other]   /'
echo ""

echo "Aktuelle Version: $LAST_VERSION"
echo "[b] major  [m] minor  [p] patch  [r] revision (Standard)  [c] release current"
read -p "Wahl [r]: " choice

if [[ "$choice" =~ ^[cC]$ ]]; then
    DIST="release"
    release_current
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
    [rR]) DIST="release";      SETUP_SUFFIX="-${NEW_REV}" ;;
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
sed -i "s/^# Onboard .*/# Onboard ${NEW_VERSION}/" README.md

# --- Versionskonsistenz prüfen ---

PY_VERSION=$(grep -E "version *= *'" setup.py | sed -E "s/.*'([^']+)'.*/\1/")

if [[ "$DIST" == "release" && "$PY_VERSION" == *".dev"* ]]; then
    echo "❌ Ungültige Release-Version (.dev nicht erlaubt)"
    exit 1
fi

# --- Commit & Push ---

git add "$CHANGELOG" setup.py README.md
git commit -m "Update version: $NEW_VERSION"
git push

# --- Tag & GitHub Release (nur bei release) ---

if [ "$DIST" = "release" ]; then
    SCRIPT_DIR="$(dirname "$0")"
    if [ -x "$SCRIPT_DIR/refresh-translations.sh" ]; then
        echo "🌐 Refreshing translations..."
        bash "$SCRIPT_DIR/refresh-translations.sh"
    else
        echo "⚠️  refresh-translations.sh not found or not executable, skipping."
    fi

    TAG="v$NEW_VERSION"
    echo "🏷  Tagging $TAG"

    git tag -s "$TAG" -m "Release $NEW_VERSION"
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
                --title "$NEW_VERSION" \
                --notes "$NOTES" \
                "$TARBALL_PATH" \
                "$ASC_PATH"
        else
            echo "⚠️  Tarball nicht gefunden: $TARBALL_PATH"
            gh release create "$TAG" \
                --title "$NEW_VERSION" \
                --notes "$NOTES"
        fi
    fi
fi

echo "✅ Fertig."
