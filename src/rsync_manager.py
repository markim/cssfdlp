"""
Rsync-based incremental file synchronization for efficient remote transfers.
"""

import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Set

from .logger import logger
from .ssh_manager import SSHOperationManager


class RsyncManager:
    """Manages rsync operations for incremental file synchronization."""

    def __init__(self, ssh_config: dict):
        self.ssh_config = ssh_config
        self.rsync_available = shutil.which("rsync") is not None

        if not self.rsync_available:
            logger.warning("rsync not available, falling back to zip-based transfers")

    def _build_rsync_command(
        self,
        source: str,
        dest: str,
        exclude_patterns: Optional[List[str]] = None,
        include_patterns: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> List[str]:
        """Build rsync command with SSH configuration."""
        cmd = ["rsync", "-avz", "--partial", "--progress"]

        if dry_run:
            cmd.append("--dry-run")

        # Add exclude patterns
        if exclude_patterns:
            for pattern in exclude_patterns:
                cmd.extend(["--exclude", pattern])

        # Add include patterns
        if include_patterns:
            for pattern in include_patterns:
                cmd.extend(["--include", pattern])

        # SSH configuration
        ssh_opts = [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            f"ConnectTimeout={30}",
            "-p",
            str(self.ssh_config["port"]),
        ]

        if self.ssh_config.get("key_file"):
            ssh_opts.extend(["-i", self.ssh_config["key_file"]])

        cmd.extend(["-e", f"ssh {' '.join(ssh_opts)}"])
        cmd.extend([source, dest])

        return cmd

    def sync_from_remote(
        self,
        remote_path: str,
        local_path: str,
        include_files: Optional[Set[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> bool:
        """Sync files from remote to local using rsync."""
        if not self.rsync_available:
            return False

        user = self.ssh_config["user"]
        host = self.ssh_config["host"]

        # Build remote source path
        remote_source = f"{user}@{host}:{remote_path}/"  # Ensure local directory exists
        os.makedirs(local_path, exist_ok=True)

        try:
            # If specific files are requested, use include/exclude patterns
            if include_files:
                # Create temporary include file
                with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".rsync") as f:
                    for file_path in include_files:
                        f.write(f"{file_path}\n")
                    include_file = f.name

                try:
                    cmd = self._build_rsync_command(
                        remote_source,
                        local_path,
                        exclude_patterns=["*"],  # Exclude everything by default
                        include_patterns=[f"--include-from={include_file}", "--include=*/"],
                    )

                    logger.info(f"Starting incremental rsync for {len(include_files)} files...")
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

                    if result.returncode == 0:
                        logger.info("Rsync completed successfully")
                        return True
                    else:
                        logger.warning(f"Rsync failed with exit code {result.returncode}")
                        logger.debug(f"Rsync stderr: {result.stderr}")
                        return False

                finally:
                    # Clean up temporary file
                    try:
                        os.unlink(include_file)
                    except Exception:
                        pass
            else:
                # Sync all files
                cmd = self._build_rsync_command(
                    remote_source, local_path, exclude_patterns=exclude_patterns
                )

                logger.info("Starting full rsync...")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

                if result.returncode == 0:
                    logger.info("Rsync completed successfully")
                    return True
                else:
                    logger.warning(f"Rsync failed with exit code {result.returncode}")
                    logger.debug(f"Rsync stderr: {result.stderr}")
                    return False

        except subprocess.TimeoutExpired:
            logger.error("Rsync operation timed out")
            return False
        except Exception as e:
            logger.error(f"Rsync operation failed: {e}")
            return False

    def estimate_transfer_size(
        self, remote_path: str, include_files: Optional[Set[str]] = None
    ) -> Optional[int]:
        """Estimate the size of data to be transferred."""
        if not self.rsync_available:
            return None

        user = self.ssh_config["user"]
        host = self.ssh_config["host"]
        remote_source = f"{user}@{host}:{remote_path}/"

        try:
            if include_files:
                # Create temporary include file
                with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".rsync") as f:
                    for file_path in include_files:
                        f.write(f"{file_path}\n")
                    include_file = f.name

                try:
                    cmd = self._build_rsync_command(
                        remote_source,
                        "/tmp/dummy",
                        exclude_patterns=["*"],
                        include_patterns=[f"--include-from={include_file}", "--include=*/"],
                        dry_run=True,
                    )

                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

                    if result.returncode == 0:
                        # Parse rsync output to estimate size
                        output = result.stdout
                        # Look for total size in rsync output
                        for line in output.split("\n"):
                            if "total size is" in line:
                                try:
                                    size_str = (
                                        line.split("total size is")[1].split()[0].replace(",", "")
                                    )
                                    return int(size_str)
                                except (IndexError, ValueError):
                                    continue

                finally:
                    try:
                        os.unlink(include_file)
                    except Exception:
                        pass

            return None

        except Exception as e:
            logger.debug(f"Failed to estimate transfer size: {e}")
            return None


class IncrementalZipCreator:
    """Creates incremental zip files containing only changed files."""

    def __init__(self, ssh_manager: SSHOperationManager):
        self.ssh_manager = ssh_manager

    def create_incremental_zip(
        self, base_remote_path: str, changed_files: Set[str], remote_zip_path: str
    ) -> bool:
        """Create zip file containing only changed files."""
        if not changed_files:
            logger.info("No changed files, skipping zip creation")
            return True

        logger.info(f"Creating incremental zip with {len(changed_files)} files")

        try:
            # Delete existing zip
            exit_status, _, _ = self.ssh_manager.exec_command_with_status(
                f"rm -f '{remote_zip_path}'", timeout=30
            )

            # Create file list for zip command
            file_list = []
            for file_path in changed_files:
                # Escape special characters for shell
                escaped_path = file_path.replace("'", "'\"'\"'")
                file_list.append(f"'{escaped_path}'")

            # Split into batches to avoid command line length limits
            batch_size = 100
            zip_created = False

            for i in range(0, len(file_list), batch_size):
                batch = file_list[i : i + batch_size]
                files_str = " ".join(batch)

                if not zip_created:
                    # Create new zip with first batch
                    zip_command = f"cd '{base_remote_path}' && zip '{os.path.basename(remote_zip_path)}' {files_str}"
                    zip_created = True
                else:
                    # Add to existing zip
                    zip_command = f"cd '{base_remote_path}' && zip -u '{os.path.basename(remote_zip_path)}' {files_str}"

                exit_status, stdout, stderr = self.ssh_manager.exec_command_with_status(
                    zip_command, timeout=600
                )

                if exit_status != 0:
                    logger.error(f"Zip creation failed for batch {i//batch_size + 1}: {stderr}")
                    return False

                logger.debug(
                    f"Added batch {i//batch_size + 1}/{(len(file_list) + batch_size - 1)//batch_size} to zip"
                )

            logger.info("Incremental zip created successfully")
            return True

        except Exception as e:
            logger.error(f"Error creating incremental zip: {e}")
            return False

    def create_full_zip(
        self, base_remote_path: str, folders_to_zip: List[str], remote_zip_path: str
    ) -> bool:
        """Create full zip file with all folders."""
        logger.info(f"Creating full zip with folders: {', '.join(folders_to_zip)}")

        try:
            # Delete existing zip
            exit_status, _, _ = self.ssh_manager.exec_command_with_status(
                f"rm -f '{remote_zip_path}'", timeout=30
            )

            # Create zip with all folders
            folders_str = " ".join(f"'{folder}'" for folder in folders_to_zip)
            zip_command = f"cd '{base_remote_path}' && zip -r '{os.path.basename(remote_zip_path)}' {folders_str}"

            exit_status, stdout, stderr = self.ssh_manager.exec_command_with_status(
                zip_command, timeout=1200
            )

            if exit_status == 0:
                logger.info("Full zip created successfully")
                return True
            else:
                logger.error(f"Full zip creation failed: {stderr}")
                return False

        except Exception as e:
            logger.error(f"Error creating full zip: {e}")
            return False
