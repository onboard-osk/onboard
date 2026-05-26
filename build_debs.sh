#!/bin/bash
# Description:
# This script automatically builds Debian (.deb) packages for Onboard.
#
# It performs the following steps:
#   1. Installs the required system and build dependencies.
#   2. Determines the Onboard version from setup.py.
#   3. Cleans previous build files.
#   4. Copies the source code to a temporary directory and creates a tarball.
#   5. Builds the Debian packages.
#   6. Moves the generated files to the "./build/debs" directory.
#
# Note: Run this script from the Onboard source directory and it requires sudo privileges.
#
# Author: Lukas Gottschall

# Get the absolute path of the script's directory
SCRIPT_PATH="$(cd -- "$(dirname "$0")" >/dev/null 2>&1; pwd -P)"
OUTPUT_DIR="$SCRIPT_PATH/build/debs"
# Define base dependencies
REQUIRED_DEPENDENCIES="python3 dpkg-dev tar wget build-essential debhelper"


while ! sudo -n true 2>/dev/null; do
    echo "This script requires sudo privileges."
    if ! sudo -v; then
        echo "Please provide your password to continue."
    fi
done

# Move to the script's directory
cd "$SCRIPT_PATH" || exit 1

# Install necessary dependencies
echo "Installing required dependencies..."

if ! sudo apt-get update; then
    echo "Error: Failed to update apt repossitory."
    exit 1
fi

if ! sudo apt-get install -y $REQUIRED_DEPENDENCIES; then
    echo "Error: Failed to install required dependencies."
    exit 1
fi

# Install build dependencies
echo "Installing build dependencies..."
if ! sudo apt-get build-dep -y .; then
    echo "Error: Failed to install build dependencies."
    exit 1
fi

# --- Get the current version from changelog ---
ONBOARD_VERSION=$(dpkg-parsechangelog -S Version)

# Extract upstream part (without the -revision) for the tarball
UPSTREAM_VERSION="${ONBOARD_VERSION%-*}"

if [[ -z "$ONBOARD_VERSION" ]]; then
    echo "Error: Could not determine Onboard version."
    exit 1
fi

echo "Clean as root via setup.py"

sudo python3 setup.py clean

echo "Building Onboard debs for version: $ONBOARD_VERSION"


# Build the Debian package
echo "Clean dpkg-buildpackage..."
if ! dpkg-buildpackage -T clean; then
    echo "Error: Failed to clean the Debian package."
    exit 1
fi

# Prepare build directory
BUILD_PATH=$(mktemp -d /tmp/onboard_deb_build_XXXXX)
echo "Create temporary build directory: $BUILD_PATH"
mkdir -p "$BUILD_PATH"
cd "$BUILD_PATH"

echo "Copy sources to $BUILD_PATH/onboard-${ONBOARD_VERSION}"
cp -Rp "$SCRIPT_PATH" "$BUILD_PATH/onboard-${ONBOARD_VERSION}"

# Create the tarball
TARBALL_NAME="onboard_${UPSTREAM_VERSION}.orig.tar.gz"
echo "Creating tarball $BUILD_PATH/$TARBALL_NAME"
tar --exclude='.git' --exclude='.gitignore' --exclude='build' -cvzf "$BUILD_PATH/$TARBALL_NAME" "onboard-${ONBOARD_VERSION}"

cd "$BUILD_PATH/onboard-${ONBOARD_VERSION}"

# Build the Debian package
echo "Building Debian package..."
if ! dpkg-buildpackage -us -uc; then
    echo "Error: Failed to build the Debian package."
    echo "Delete $BUILD_PATH"
    rm -Rf "$BUILD_PATH"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
echo "Copy files to $OUTPUT_DIR"
cd "$BUILD_PATH"
for FILE in *; do
    if [ "$(basename "$FILE")" != "onboard-${ONBOARD_VERSION}" ]; then
        mv "$FILE" "$OUTPUT_DIR"
    fi
done
echo "Delete $BUILD_PATH"
rm -Rf "$BUILD_PATH"

# Move to the parent directory
cd "$OUTPUT_DIR" || exit 1

# Generate the metadata file Packages for the repository
echo "Generating metadata file Packages..."
printf "gnome-shell-extension-onboard optional misc\nonboard optional utils\nonboard-common optional utils\nonboard-data optional utils\n" > override
if ! dpkg-scanpackages . override > Packages; then
    echo "Error: Failed to generate Packages."
    exit 1
fi

# Final message
echo "Onboard $ONBOARD_VERSION Debian packages successfully built and saved in: $OUTPUT_DIR"
