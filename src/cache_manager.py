"""
Cache management for processed files and remote downloads.
Simplified to use a single cache directory approach.
"""

import json
import os

from .config import CACHE_DIR, PROCESSED_CACHE, REMOTE_ZIP_CACHE
from .logger import logger


def ensure_cache_dirs():
    """Ensure all cache directories exist."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(REMOTE_ZIP_CACHE, exist_ok=True)
    os.makedirs(PROCESSED_CACHE, exist_ok=True)


def get_cached_zip_path(remote_host, remote_path):
    """Get the cached zip file path for a remote location."""
    # Create a unique filename based on remote host and path
    identifier = f"{remote_host}_{remote_path}".replace("/", "_").replace("\\", "_")
    safe_identifier = "".join(c for c in identifier if c.isalnum() or c in "-_")
    return os.path.join(REMOTE_ZIP_CACHE, f"{safe_identifier}.zip")


def get_cached_processed_path(source_info):
    """Get the cached processed files path for a source using simple naming."""
    # Return the actual output directory, not a separate cache directory
    # This ensures cache metadata is stored with the processed files
    from .config import DEFAULT_OUTPUT_DIR

    return DEFAULT_OUTPUT_DIR


def get_cached_processed_path_with_fallback(source_info):
    """Get cached processed path - simplified version without legacy fallback."""
    return get_cached_processed_path(source_info)


def store_remote_md5s(remote_md5s, processed_dir):
    """Store remote MD5 hashes for future comparison."""
    md5_file = os.path.join(processed_dir, ".remote_md5s.json")
    try:
        os.makedirs(processed_dir, exist_ok=True)
        with open(md5_file, "w") as f:
            json.dump(remote_md5s, f, indent=2)
        logger.debug(f"Stored {len(remote_md5s)} remote MD5 hashes")
    except Exception as e:
        logger.warning(f"Failed to store remote MD5s: {e}")


def load_remote_md5s(processed_dir):
    """Load stored remote MD5 hashes."""
    md5_file = os.path.join(processed_dir, ".remote_md5s.json")
    try:
        if os.path.exists(md5_file):
            with open(md5_file, "r") as f:
                remote_md5s = json.load(f)
            logger.debug(f"Loaded {len(remote_md5s)} stored remote MD5 hashes")
            return remote_md5s
    except Exception as e:
        logger.warning(f"Failed to load remote MD5s: {e}")

    return {}
