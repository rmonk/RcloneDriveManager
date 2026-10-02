#!/usr/bin/env bash
# BSD 3-Clause License

# Copyright (c) 2022, Marcus Behel
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.

# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.

# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.

# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# Create an AppImage with its own Python, PySide6 and rclone.
# Requires: python3 with PySide6 (for compile.py), python-appimage, curl, unzip, sha256sum, ldd
# Output: dist/RcloneDriveManager-<version>-x86_64.AppImage

set -euo pipefail

DIR=$(realpath "$(dirname "$0")")
ROOT=$(realpath "$DIR/../..")

PYTHON=${PYTHON:-python3}
PYTHON_APPIMAGE=${PYTHON_APPIMAGE:-python-appimage}
PY_VERSION=${PY_VERSION:-3.13}
LINUX_TAG=${LINUX_TAG:-manylinux_2_28_x86_64}

VERSION=$(tr -d '[:space:]' < "$ROOT/res/version.txt")
RCLONE_VERSION=$(tr -d '[:space:]' < "$DIR/rclone-version.txt")
RCLONE_ZIP="rclone-${RCLONE_VERSION}-linux-amd64"

BUILD="$DIR/build"
RECIPE="$BUILD/rclone-drive-manager"
EXTRA="$BUILD/extra"

echo "Building RcloneDriveManager $VERSION with rclone $RCLONE_VERSION..."
rm -rf "$BUILD"
mkdir -p "$RECIPE" "$EXTRA/opt/rclone-drive-manager" "$EXTRA/usr/bin" \
    "$EXTRA/usr/share/doc/rclone-drive-manager" "$EXTRA/usr/share/doc/rclone"

echo "Compiling UI and resources..."
"$PYTHON" "$ROOT/compile.py"

echo "Staging recipe..."
cp "$DIR/requirements.txt" "$DIR/entrypoint.sh" "$DIR/rclone-drive-manager.desktop" \
    "$DIR/rclone-drive-manager.appdata.xml" "$RECIPE/"
cp "$ROOT/res/icon.png" "$RECIPE/rclone-drive-manager.png"
cp "$ROOT"/src/*.py "$EXTRA/opt/rclone-drive-manager/"
cp "$ROOT/LICENSE" "$EXTRA/usr/share/doc/rclone-drive-manager/copyright"

echo "Downloading rclone $RCLONE_VERSION..."
pushd "$BUILD" > /dev/null
curl -fsSL -o "$RCLONE_ZIP.zip" "https://downloads.rclone.org/$RCLONE_VERSION/$RCLONE_ZIP.zip"
curl -fsSL -o SHA256SUMS "https://downloads.rclone.org/$RCLONE_VERSION/SHA256SUMS"
grep " $RCLONE_ZIP.zip\$" SHA256SUMS | sha256sum -c -
unzip -q "$RCLONE_ZIP.zip"
install -m 755 "$RCLONE_ZIP/rclone" "$EXTRA/usr/bin/rclone"
cp "$RCLONE_ZIP/README.txt" "$EXTRA/usr/share/doc/rclone/"

echo "Building AppDir..."
"$PYTHON_APPIMAGE" build app --no-packaging -p "$PY_VERSION" -l "$LINUX_TAG" "$RECIPE" \
    -x "$EXTRA/opt" "$EXTRA/usr"
APPDIR="$BUILD/RcloneDriveManager-x86_64"

echo "Removing unused Qt components..."
"$PYTHON" "$DIR/prune.py" "$APPDIR"

echo "Packaging AppImage..."
curl -fsSL -o appimagetool \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x appimagetool
mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/RcloneDriveManager-$VERSION-x86_64.AppImage"
ARCH=x86_64 ./appimagetool --appimage-extract-and-run --no-appstream "$APPDIR" "$OUT"
popd > /dev/null

echo "Created $OUT"
