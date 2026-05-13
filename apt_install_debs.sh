#!/bin/sh
# Description:
# This script sets up a temporary local APT repository for the Onboard packages.
# The script searches for the required deb packages in one of the following locations:
#   - The directory provided as the first parameter ($1)
#   - The current working directory
#   - The directory where the script is located
#   - A ".build/debs" subdirectory (relative to either the script's location or the current working directory)
# After locating the packages, it configures the local repository, updates the package index,
# and installs the necessary Onboard packages (onboard, onboard-data, and if GNOME Shell is present,
# gnome-shell-extension-onboard). Once installation is complete, the temporary repository configuration is removed.
#
# Author: Lukas Gottschall
#
# Note: This script must be executed as root.

# Get the absolute path of the script's directory
SCRIPT_PATH="$(
    cd -- "$(dirname "$0")" >/dev/null 2>&1
    pwd -P
)"

# Function to provide a list of directories to check
check_directories() {
    echo "$DEB_DIR"
    echo "$SCRIPT_PATH"
    echo "$(pwd)"
    echo "$SCRIPT_PATH/build/debs"
    echo "$(pwd)/build/debs"
}

# Check if the script is run as root
if [ "$(id -u)" = "0" ]; then
    # Set the default directory
    DEB_DIR="${1:-$SCRIPT_PATH}"
    echo "Remove Onboard packages."
    # Install the Onboard packages
    if which gnome-shell >/dev/null 2>&1; then
        # Remove installed Onboard packages
        apt-get -y remove onboard onboard-data onboard-common gnome-shell-extension-onboard
    else
        # Remove installed Onboard packages
        apt-get -y remove onboard onboard-data onboard-common
    fi
    if [ "$DEB_DIR" != "remove" ]; then
        # Search for the file
        DEB_FOUND=false
        for dir in $(check_directories); do
            if find "$dir" -maxdepth 1 -name "onboard-common_*_all.deb" | grep -q .; then
                DEB_DIR="$dir"
                DEB_FOUND=true
                break
            fi
        done

        # Check if the file was found
        if [ "$DEB_FOUND" = false ]; then
            echo "Error: Unable to find onboard debs. Please run $0 /path/to/onboard/debs"
            exit 1
        fi
				ONBOARD_VERSION="$(basename "$(find "$DEB_DIR" -maxdepth 1 -name 'onboard_*_*.deb' | head -n1)" | sed -E 's/^onboard_([^_]+)_.+$/\1/')"
				
        echo "Onboard debs found in: $DEB_DIR"
				echo "Detected Onboard version: $ONBOARD_VERSION"

				ONBOARD_APT_REPO="/etc/apt/sources.list.d/onboardlocalrepo.list"
				
        # Configure a local APT repository
				echo "deb [trusted=yes] file:$DEB_DIR/ ./" > "$ONBOARD_APT_REPO"


				# Update package index for the temporary local repository
				apt-get update \
						-o Dir::Etc::sourcelist="$ONBOARD_APT_REPO" \
						-o Dir::Etc::sourceparts="-"
								
				# Install the Onboard packages
				if which gnome-shell >/dev/null 2>&1; then
						echo "GNOME Shell is installed."
						apt-get -y install \
								-o Dir::Etc::sourcelist="$ONBOARD_APT_REPO" \
								-o Dir::Etc::sourceparts="-" \
								onboard="$ONBOARD_VERSION" \
								onboard-data="$ONBOARD_VERSION" \
								onboard-common="$ONBOARD_VERSION" \
								gnome-shell-extension-onboard="$ONBOARD_VERSION"
				else
						echo "GNOME Shell is not installed."
						apt-get -y install \
								-o Dir::Etc::sourcelist="$ONBOARD_APT_REPO" \
								-o Dir::Etc::sourceparts="-" \
								onboard="$ONBOARD_VERSION" \
								onboard-data="$ONBOARD_VERSION" \
								onboard-common="$ONBOARD_VERSION"
				fi
        
        echo "Updating GLib schemas..."
        glib-compile-schemas "/usr/share/glib-2.0/schemas" || true  # Run it, but don't fail if missing
        echo "Update the icon cache..."
        for theme in hicolor HighContrast ubuntu-mono-dark ubuntu-mono-light; do
            gtk-update-icon-cache -f "/usr/share/icons/$theme" || true
        done

        # Remove the temporary local repository configuration
        rm "${ONBOARD_APT_REPO}"
    fi
else
    while ! sudo -n true 2>/dev/null; do
        echo "This script requires sudo privileges."
        if ! sudo -v; then
            echo "Please provide your password to continue."
        fi
    done
    sudo "$0" "$@"
fi
