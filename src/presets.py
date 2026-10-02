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

# Recommended rclone mount options for common use cases.
# Based on the rclone mount documentation (https://rclone.org/commands/rclone_mount/)
# with cache sizes chosen for desktops / laptops rather than media servers.

from typing import List, NamedTuple, Optional


class Preset(NamedTuple):
    id: str
    label: str
    description: str
    args: str


CUSTOM = "custom"
DEFAULT = "general"

PRESETS: List[Preset] = [
    Preset(
        "general", "General use (recommended)",
        "Behaves like a local folder for any application (open, edit and save in place). "
        "Recently used files are cached on disk, up to 10 GB for 24 hours, keeping at least "
        "5 GB of disk free.",
        "--vfs-cache-mode full\n"
        "--vfs-cache-max-size 10G\n"
        "--vfs-cache-max-age 24h\n"
        "--vfs-cache-min-free-space 5G"),
    Preset(
        "streaming", "Media streaming",
        "For playing video and music (e.g. Plex, Jellyfin, VLC). Reads ahead for smooth "
        "playback and caches up to 50 GB for a week so rewatching doesn't download again. "
        "Folder listings are cached for an hour; remotes that support change polling still "
        "show new files within a minute.",
        "--vfs-cache-mode full\n"
        "--vfs-cache-max-size 50G\n"
        "--vfs-cache-max-age 168h\n"
        "--vfs-cache-min-free-space 10G\n"
        "--vfs-read-ahead 512M\n"
        "--buffer-size 64M\n"
        "--dir-cache-time 1h"),
    Preset(
        "light", "Light (low disk use)",
        "Reads stream directly from the remote and only files being written are cached "
        "(up to 1 GB). Works with most applications, but files are downloaded again each "
        "time they are opened.",
        "--vfs-cache-mode writes\n"
        "--vfs-cache-max-size 1G"),
    Preset(
        "readonly", "Read-only browsing",
        "Prevents any changes to the remote. Good for backups, archives and photo libraries. "
        "Viewed files are cached (up to 5 GB) so browsing them again is fast.",
        "--read-only\n"
        "--vfs-cache-mode full\n"
        "--vfs-cache-max-size 5G\n"
        "--vfs-cache-max-age 24h"),
    Preset(
        "object", "S3 / object storage",
        "Tuned for S3, Swift, B2 and similar object stores: skips slow modification time "
        "lookups and reads in parallel streams. File modification times are not preserved.",
        "--vfs-cache-mode full\n"
        "--vfs-cache-max-size 10G\n"
        "--vfs-cache-max-age 24h\n"
        "--vfs-fast-fingerprint\n"
        "--no-modtime\n"
        "--vfs-read-chunk-size 4M\n"
        "--vfs-read-chunk-streams 16"),
]

CUSTOM_DESCRIPTION = "Enter your own rclone mount flags, one or more per line."


def get_preset(preset_id: str) -> Optional[Preset]:
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    return None


def normalize_args(args: str) -> str:
    return " ".join(args.split())


def match_preset(args: str) -> str:
    """Preset id whose args equal these args, otherwise CUSTOM (for configs saved before presets)."""
    for preset in PRESETS:
        if normalize_args(preset.args) == normalize_args(args):
            return preset.id
    return CUSTOM
