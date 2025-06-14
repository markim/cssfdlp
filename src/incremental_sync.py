"""
Incremental remote file change detection using timestamps and MD5 hashes.
Optimizes remote operations by only checking files that have changed.
"""

import json
import os
from typing import Dict, Optional, Set, Tuple

from .config import ALLOWED_FASTDL_FOLDERS
from .logger import logger
from .ssh_manager import SSHOperationManager


class IncrementalChangeDetector:
    """Detects changes in remote files using timestamps and cached MD5s."""

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.timestamp_cache_file = os.path.join(cache_dir, ".remote_timestamps.json")
        self.md5_cache_file = os.path.join(cache_dir, ".remote_md5s.json")

    def _load_timestamp_cache(self) -> Dict[str, float]:
        """Load cached file timestamps."""
        if os.path.exists(self.timestamp_cache_file):
            try:
                with open(self.timestamp_cache_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"Error loading timestamp cache: {e}")
        return {}

    def _save_timestamp_cache(self, timestamps: Dict[str, float]):
        """Save file timestamps to cache."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self.timestamp_cache_file, "w") as f:
                json.dump(timestamps, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save timestamp cache: {e}")

    def _load_md5_cache(self) -> Dict[str, str]:
        """Load cached MD5 hashes."""
        if os.path.exists(self.md5_cache_file):
            try:
                with open(self.md5_cache_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"Error loading MD5 cache: {e}")
        return {}

    def _save_md5_cache(self, md5s: Dict[str, str]):
        """Save MD5 hashes to cache."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self.md5_cache_file, "w") as f:
                json.dump(md5s, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save MD5 cache: {e}")

    def get_remote_file_timestamps(
        self, ssh_manager: SSHOperationManager, base_remote_path: str
    ) -> Dict[str, float]:
        """Get modification timestamps for all remote files."""
        logger.info("Getting remote file timestamps...")
        timestamps = {}

        for folder in ALLOWED_FASTDL_FOLDERS:
            folder_path = f"{base_remote_path}/{folder}"

            # Check if folder exists
            exit_status, _, _ = ssh_manager.exec_command_with_status(
                f"test -d '{folder_path}' && echo 'EXISTS' || echo 'MISSING'", timeout=30
            )

            if exit_status != 0:
                logger.warning(f"Cannot access folder: {folder}")
                continue

            # Get file timestamps using find with printf
            find_cmd = f"""
            cd '{base_remote_path}' && find '{folder}' -type f -printf '%p\\t%T@\\n' 2>/dev/null
            """

            exit_status, stdout, stderr = ssh_manager.exec_command_with_status(
                find_cmd, timeout=300
            )

            if exit_status == 0 and stdout:
                for line in stdout.split("\n"):
                    if not line.strip():
                        continue

                    parts = line.strip().split("\t")
                    if len(parts) == 2:
                        file_path, timestamp_str = parts
                        try:
                            timestamp = float(timestamp_str)
                            timestamps[file_path] = timestamp
                        except ValueError:
                            logger.debug(f"Invalid timestamp for {file_path}: {timestamp_str}")
            else:
                logger.warning(f"Failed to get timestamps for {folder}: {stderr}")

        logger.info(f"Retrieved timestamps for {len(timestamps)} remote files")
        return timestamps

    def find_changed_files(self, current_timestamps: Dict[str, float]) -> Set[str]:
        """Find files that have changed since last check."""
        cached_timestamps = self._load_timestamp_cache()
        changed_files = set()

        # Find new and modified files
        for file_path, current_time in current_timestamps.items():
            if file_path not in cached_timestamps:
                # New file
                changed_files.add(file_path)
                logger.debug(f"New file: {file_path}")
            elif current_time > cached_timestamps[file_path]:
                # Modified file
                changed_files.add(file_path)
                logger.debug(
                    f"Modified file: {file_path} (was: {cached_timestamps[file_path]}, now: {current_time})"
                )

        # Find deleted files (in cache but not in current)
        deleted_files = set(cached_timestamps.keys()) - set(current_timestamps.keys())
        if deleted_files:
            logger.info(f"Found {len(deleted_files)} deleted files")
            for file_path in deleted_files:
                logger.debug(f"Deleted file: {file_path}")

        logger.info(
            f"Found {len(changed_files)} changed files out of {len(current_timestamps)} total"
        )
        return changed_files

    def get_incremental_md5s(
        self, ssh_manager: SSHOperationManager, base_remote_path: str, changed_files: Set[str]
    ) -> Dict[str, str]:
        """Get MD5 hashes only for changed files."""
        if not changed_files:
            logger.info("No files changed, using cached MD5s")
            return self._load_md5_cache()

        logger.info(f"Calculating MD5s for {len(changed_files)} changed files...")

        # Load existing MD5 cache
        all_md5s = self._load_md5_cache()
        new_md5s = {}  # Process files in batches to avoid command line length limits
        batch_size = 50
        changed_list = list(changed_files)

        for i in range(0, len(changed_list), batch_size):
            batch = changed_list[i : i + batch_size]

            # Create MD5 command for this batch
            file_list = " ".join(f"'{file_path}'" for file_path in batch)
            md5_command = f"""
            cd '{base_remote_path}' &&
            for file in {file_list}; do
                if [ -f "$file" ]; then
                    md5sum "$file" 2>/dev/null || echo "ERROR: $file"
                fi
            done
            """

            exit_status, stdout, stderr = ssh_manager.exec_command_with_status(
                md5_command, timeout=300
            )

            if exit_status == 0 and stdout:
                for line in stdout.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("ERROR:"):
                        continue

                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        md5_hash, file_path = parts
                        if len(md5_hash) == 32 and all(
                            c in "0123456789abcdef" for c in md5_hash.lower()
                        ):
                            new_md5s[file_path] = md5_hash.lower()
                        else:
                            logger.debug(f"Invalid MD5 format: {md5_hash} for {file_path}")

            logger.debug(
                f"Processed batch {i//batch_size + 1}/{(len(changed_list) + batch_size - 1)//batch_size}"
            )

        # Update the complete MD5 cache
        all_md5s.update(new_md5s)

        # Remove MD5s for files that no longer exist
        current_files = set()
        exit_status, stdout, _ = ssh_manager.exec_command_with_status(
            f"cd '{base_remote_path}' && find {' '.join(ALLOWED_FASTDL_FOLDERS)} -type f 2>/dev/null",
            timeout=300,
        )

        if exit_status == 0 and stdout:
            current_files = set(line.strip() for line in stdout.split("\n") if line.strip())

        # Remove MD5s for deleted files
        deleted_files = set(all_md5s.keys()) - current_files
        for file_path in deleted_files:
            del all_md5s[file_path]

        logger.info(
            f"Updated MD5s: {len(new_md5s)} new, {len(deleted_files)} removed, {len(all_md5s)} total"
        )
        return all_md5s

    def update_caches(self, timestamps: Dict[str, float], md5s: Dict[str, str]):
        """Update both timestamp and MD5 caches."""
        self._save_timestamp_cache(timestamps)
        self._save_md5_cache(md5s)
        logger.debug("Updated timestamp and MD5 caches")

    def should_update_remote_zip(
        self, ssh_manager: SSHOperationManager, base_remote_path: str
    ) -> Tuple[bool, Optional[Set[str]]]:
        """Check if remote zip needs updating and return changed files."""
        try:
            current_timestamps = self.get_remote_file_timestamps(ssh_manager, base_remote_path)
            if not current_timestamps:
                logger.warning("No remote files found")
                return True, None

            changed_files = self.find_changed_files(current_timestamps)

            if not changed_files:
                logger.info("No files have changed since last check")
                return False, set()

            logger.info(f"Found {len(changed_files)} changed files, update needed")
            return True, changed_files

        except Exception as e:
            logger.warning(f"Error checking for changes: {e}")
            return True, None  # Assume update needed on error
