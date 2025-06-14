"""
Configuration constants and settings for cssfdlp.
"""

import os

# Version information
VERSION = "1.0.0"

# Global constant for allowed folders
ALLOWED_FASTDL_FOLDERS = ["maps", "materials", "models", "sound"]

# File extensions that should be compressed with bzip2
COMPRESS_EXTENSIONS = [
    ".bsp",  # Maps
    ".nav",  # Navigation meshes
    ".ain",  # AI nodes
    ".wav",  # Audio files
    # '.mp3',  # MP3s already compressed, minimal benefit per Valve wiki
    ".ogg",  # Audio files
]

# Cache directories - these never expire
CACHE_DIR = "./cache"
REMOTE_ZIP_CACHE = os.path.join(CACHE_DIR, "remote_zips")
PROCESSED_CACHE = os.path.join(CACHE_DIR, "processed_files")

# Default configuration values
DEFAULT_OUTPUT_DIR = "./processed_cstrike"
DEFAULT_REMOTE_PORT = 22
DEFAULT_REMOTE_PATH = "/path/to/cstrike"

# Performance settings
DEFAULT_UPLOAD_WORKERS = 10
DEFAULT_TIMEOUT = 30
DEFAULT_ZIP_TIMEOUT = 600  # 10 minutes
DEFAULT_MD5_TIMEOUT = 600  # 10 minutes
