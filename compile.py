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


import shutil
import subprocess
import os
import sys

script_dir = os.path.dirname(os.path.realpath(__file__))


def find_tool(name):
    """Return the command for a Qt tool (uic / rcc) that generates Python code."""
    # pip installs pyside6-uic / pyside6-rcc wrappers
    wrapper = shutil.which("pyside6-" + name)
    if wrapper is not None:
        return [wrapper]
    # Distro packages (e.g. Fedora python3-pyside6) only ship the raw Qt tools
    try:
        import PySide6
    except ImportError:
        return None
    tool = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "libexec", name)
    if os.access(tool, os.X_OK):
        return [tool, "-g", "python"]
    return None


uic = find_tool("uic")
rcc = find_tool("rcc")
if uic is None or rcc is None:
    print("No PySide6 UIC and RCC found. Exiting.")
    sys.exit(1)

# Remove old generated files
for dirpath, dirnames, filenames in os.walk(os.path.join(script_dir, "src")):
    for src_file in filenames:
        if src_file.endswith('.py') and (src_file.startswith("ui_") or src_file.endswith("_rc.py")):
            print("[Deleting]: {0}".format(src_file))
            os.remove(os.path.join(dirpath, src_file))

# Compile UI files
for dirpath, dirnames, filenames in os.walk(os.path.join(script_dir, "ui")):
    for src_file in filenames:
        if src_file.endswith('.ui'):
            dest_file = "ui_" + src_file.replace(".ui", ".py")
            src_path = os.path.join(dirpath, src_file)
            dest_path = os.path.join(script_dir, "src", dest_file)
            print("[Compiling]: {0} --> {1}".format(src_file, dest_file))
            subprocess.run(uic + [src_path, "-o", dest_path], check=True)

# Compile QRC files
for dirpath, dirnames, filenames in os.walk(os.path.join(script_dir, "res")):
    for src_file in filenames:
        if src_file.endswith('.qrc'):
            dest_file = src_file.replace(".qrc", "") + "_rc.py"
            src_path = os.path.join(dirpath, src_file)
            dest_path = os.path.join(script_dir, "src", dest_file)
            print("[Compiling]: {0} --> {1}".format(src_file, dest_file))
            subprocess.run(rcc + [src_path, "-o", dest_path], check=True)
