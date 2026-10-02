#! /bin/bash
exec "${APPDIR}/usr/bin/python{{ python-version }}" "${APPDIR}/opt/rclone-drive-manager/main.py" "$@"
