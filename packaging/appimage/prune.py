#!/usr/bin/env python3
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

# Remove the parts of PySide6 that a QtWidgets tray app does not use
# (QML / Quick, Designer, dev tools, ...) from an AppDir.
# Usage: prune.py AppDir

import glob
import os
import shutil
import subprocess
import sys

# Python binding modules to keep
KEEP_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtDBus"}

# Qt shared libraries to keep (prefix match on libQt6<name>.so)
KEEP_LIBS = {"Core", "Gui", "Widgets", "DBus", "WaylandClient", "XcbQpa", "OpenGL", "Svg"}

# Plugin directories to keep
KEEP_PLUGINS = {
    "platforms", "platformthemes", "platforminputcontexts", "iconengines", "imageformats",
    "wayland-decoration-client", "wayland-graphics-integration-client",
    "wayland-shell-integration", "xcbglintegrations",
}

# Platform plugins to keep
KEEP_PLATFORMS = {"libqxcb.so", "libqwayland.so", "libqoffscreen.so", "libqminimal.so"}

# Directly under PySide6/
REMOVE_IN_PYSIDE = [
    "assistant", "designer", "linguist", "lrelease", "lupdate", "qmlformat", "qmlls",
    "qmllint", "qmlcachegen", "qmlimportscanner", "qmltyperegistrar", "balsam", "balsamui",
    "metaobjectdump", "include", "typesystems", "scripts", "Qt/qml", "Qt/metatypes",
    "Qt/libexec",
]

# Libraries every kept Python module and platform plugin must still resolve
MUST_RESOLVE = ["QtCore.abi3.so", "QtGui.abi3.so", "QtWidgets.abi3.so", "QtDBus.abi3.so",
                "Qt/plugins/platforms/libqxcb.so", "Qt/plugins/platforms/libqwayland.so"]


def remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def missing_qt_deps(path, lib_dir):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = lib_dir
    res = subprocess.run(["ldd", path], capture_output=True, text=True, env=env)
    missing = set()
    for line in (res.stdout + res.stderr).splitlines():
        if "not found" not in line:
            continue
        if "=>" in line:
            # "libQt6Foo.so.6 => not found"
            missing.add(line.split()[0])
        else:
            # "<path>: <lib>: version `Qt_6_PRIVATE_API' not found (required by ...)"
            missing.add("{} ({})".format(os.path.basename(line.split(":")[1].strip()),
                                         line.split("`")[1].split("'")[0] if "`" in line else "version"))
    return sorted(m for m in missing
                  if any(k in m for k in ("libQt6", "libicu", "libpyside", "libshiboken")))


def main(appdir):
    found = glob.glob(os.path.join(appdir, "opt", "python*", "lib", "python*", "site-packages", "PySide6"))
    if len(found) != 1:
        print("PySide6 not found in {}".format(appdir))
        return 1
    pyside = found[0]
    qt = os.path.join(pyside, "Qt")
    lib_dir = os.path.join(qt, "lib")

    for name in REMOVE_IN_PYSIDE:
        remove(os.path.join(pyside, name))

    for path in glob.glob(os.path.join(pyside, "*.abi3.so")):
        if os.path.basename(path).split(".")[0] not in KEEP_MODULES:
            remove(path)
    for path in glob.glob(os.path.join(pyside, "*.pyi")):
        remove(path)

    for path in glob.glob(os.path.join(lib_dir, "libQt6*")):
        name = os.path.basename(path)[len("libQt6"):].split(".")[0]
        if name not in KEEP_LIBS:
            remove(path)

    plugins = os.path.join(qt, "plugins")
    for path in glob.glob(os.path.join(plugins, "*")):
        if os.path.basename(path) not in KEEP_PLUGINS:
            remove(path)
    for path in glob.glob(os.path.join(plugins, "platforms", "*")):
        if os.path.basename(path) not in KEEP_PLATFORMS:
            remove(path)

    for path in glob.glob(os.path.join(qt, "translations", "*")):
        if not os.path.basename(path).startswith("qtbase_"):
            remove(path)

    for path in glob.glob(os.path.join(appdir, "usr", "bin", "pyside6-*")):
        remove(path)

    # Drop any plugin that depended on something removed above
    for path in glob.glob(os.path.join(plugins, "**", "*.so"), recursive=True):
        missing = missing_qt_deps(path, lib_dir)
        if missing:
            print("[Prune]: {} (needs {})".format(os.path.relpath(path, qt), ", ".join(missing)))
            remove(path)

    ok = True
    for rel in MUST_RESOLVE:
        path = os.path.join(pyside, rel)
        if not os.path.exists(path):
            print("[Error]: {} was removed".format(rel))
            ok = False
            continue
        missing = missing_qt_deps(path, lib_dir)
        if missing:
            print("[Error]: {} is missing {}".format(rel, ", ".join(missing)))
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: prune.py AppDir")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
