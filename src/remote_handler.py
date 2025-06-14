"""
Remote server handling for SSH connections, zip creation, and downloads.
Enhanced with connection pooling, incremental sync, and performance optimizations.
"""

import os
import stat
import zipfile

import paramiko
import requests

from .cache_manager import get_cached_processed_path_with_fallback, get_cached_zip_path
from .config import ALLOWED_FASTDL_FOLDERS, DEFAULT_TIMEOUT
from .config_validator import performance_metrics
from .incremental_sync import IncrementalChangeDetector
from .logger import logger
from .rsync_manager import IncrementalZipCreator, RsyncManager
from .ssh_manager import SSHOperationManager, get_ssh_connection


def should_update_remote_zip(
    remote_host, remote_user, remote_password, remote_key_file, remote_port, remote_path
):
    """Check if remote zip needs to be updated by comparing file listings.
    Optimized with better timestamp comparison and caching."""
    cached_zip = get_cached_zip_path(remote_host, remote_path)

    if not os.path.exists(cached_zip):
        logger.info("No cached zip found, will create new one")
        return True

    logger.info("Checking if remote files have changed...")

    try:
        # Connect to remote server to check file modifications
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if remote_key_file:
            ssh.connect(
                remote_host, port=remote_port, username=remote_user, key_filename=remote_key_file
            )
        else:
            ssh.connect(
                remote_host, port=remote_port, username=remote_user, password=remote_password
            )

        # Get the modification time of the most recent file in allowed folders using optimized command
        folders_str = " ".join([f"'{remote_path}/{folder}'" for folder in ALLOWED_FASTDL_FOLDERS])
        find_cmd = f"find {folders_str} -type f -printf '%T@\\n' 2>/dev/null | sort -n | tail -1"

        stdin, stdout, stderr = ssh.exec_command(find_cmd, timeout=60)
        result = stdout.read().decode().strip()

        ssh.close()

        if not result:
            logger.info("Could not determine remote file times, will update zip")
            return True

        try:
            latest_remote_mtime = float(result)
        except ValueError:
            logger.info("Invalid timestamp format, will update zip")
            return True

        # Compare with cached zip modification time
        cached_zip_mtime = os.path.getmtime(cached_zip)

        if latest_remote_mtime > cached_zip_mtime:
            logger.info(
                f"Remote files are newer than cached zip, will update (remote: {latest_remote_mtime}, cache: {cached_zip_mtime})"
            )
            return True
        else:
            logger.info("Cached zip is up to date")
            return False

    except Exception as e:
        logger.warning(f"Could not check remote file times: {e}")
        logger.info("Will update zip to be safe")
        return True


def get_remote_file_md5s(ssh, base_remote_path, allowed_folders):
    """Get MD5 hashes for all files in allowed folders on the remote server.
    Uses optimized approach with parallel processing and better error handling."""
    logger.info("Calculating MD5 hashes for remote files...")
    remote_md5s = {}

    for folder in allowed_folders:
        folder_path = f"{base_remote_path}/{folder}"
        logger.info(f"Processing {folder} folder...")

        # Check if folder exists first
        folder_check_cmd = f"test -d '{folder_path}' && echo 'EXISTS' || echo 'MISSING'"
        stdin, stdout, stderr = ssh.exec_command(folder_check_cmd, timeout=30)
        result = stdout.read().decode().strip()

        if result != "EXISTS":
            logger.warning(f"Folder {folder} does not exist or is not accessible")
            continue

        # Use find with md5sum for better performance and error handling
        # Use a more robust command that handles filenames with spaces and special characters
        md5_command = f"""
        cd '{base_remote_path}' && find '{folder}' -type f -print0 | while IFS= read -r -d '' file; do
            if [ -f "$file" ]; then                md5sum "$file" 2>/dev/null || echo "ERROR: $file"
            fi
        done
        """

        logger.debug(f"Executing MD5 command for {folder}...")
        stdin, stdout, stderr = ssh.exec_command(md5_command, timeout=600)  # 10 minute timeout

        # exit_status is checked for command completion
        stdout.channel.recv_exit_status()
        output = stdout.read().decode(errors="ignore").strip()
        stderr_output = stderr.read().decode(errors="ignore").strip()

        if stderr_output:
            logger.debug(f"MD5 command stderr for {folder}: {stderr_output}")

        if output:
            error_count = 0
            processed_count = 0

            for line in output.split("\n"):
                line = line.strip()
                if not line:
                    continue

                if line.startswith("ERROR:"):
                    error_count += 1
                    logger.debug(f"Skipped problematic file: {line}")
                    continue

                # Parse "hash  filename" format
                parts = line.split(None, 1)  # Split on whitespace, max 2 parts
                if len(parts) == 2:
                    md5_hash, file_path = parts

                    # Validate MD5 hash format
                    if len(md5_hash) == 32 and all(
                        c in "0123456789abcdef" for c in md5_hash.lower()
                    ):
                        # Convert absolute path to relative path from base_remote_path
                        rel_path = file_path
                        remote_md5s[rel_path] = md5_hash.lower()
                        processed_count += 1
                        logger.debug(f"Remote MD5: {rel_path} = {md5_hash.lower()}")
                    else:
                        logger.debug(f"Invalid MD5 hash format: {md5_hash} for {file_path}")
                        error_count += 1
                else:
                    logger.debug(f"Could not parse MD5 line: {line}")
                    error_count += 1

            if processed_count > 0:
                logger.info(f"Processed {processed_count} files in {folder}/ folder")
            if error_count > 0:
                logger.warning(f"Encountered {error_count} errors processing {folder}/ folder")
        else:
            logger.warning(f"No output from MD5 command for {folder}")

    logger.info(f"Calculated MD5 hashes for {len(remote_md5s)} remote files total")
    return remote_md5s


def compare_with_cached_md5s(remote_md5s, processed_dir):
    """Compare remote MD5s with cached processed files to determine what needs updating.
    Now with improved MD5 parsing and validation."""
    from .config import COMPRESS_EXTENSIONS

    files_to_update = set()

    if not os.path.exists(processed_dir):
        logger.info("No processed directory found, all files need processing")
        return set(remote_md5s.keys())

    logger.info("Comparing remote files with cached processed files...")

    for remote_file, remote_md5 in remote_md5s.items():
        # Ensure remote MD5 is lowercase and validated
        remote_md5 = remote_md5.lower()
        if len(remote_md5) != 32 or not all(c in "0123456789abcdef" for c in remote_md5):
            logger.warning(f"Invalid remote MD5 format for {remote_file}: {remote_md5}")
            files_to_update.add(remote_file)
            continue

        # Determine the expected processed file path
        base_name, ext = os.path.splitext(remote_file)

        # Check if this file would be compressed
        if ext.lower() in COMPRESS_EXTENSIONS:
            processed_file = os.path.join(processed_dir, base_name + ext + ".bz2")
            md5_file = processed_file + ".md5"
        else:
            processed_file = os.path.join(processed_dir, remote_file)
            md5_file = processed_file + ".md5"

        # Check if processed file and its MD5 exist
        if os.path.exists(processed_file) and os.path.exists(md5_file):
            # Read the local MD5 of the original file (before compression)
            try:
                with open(md5_file, "r") as f:
                    md5_content = f.read().strip()

                # Handle different line endings and parse consistently
                md5_content = md5_content.replace("\r\n", "\n").replace("\r", "\n")
                if not md5_content:
                    logger.debug(f"Empty MD5 file for {remote_file}")
                    files_to_update.add(remote_file)
                    continue

                # Split on whitespace and take first part as hash
                parts = md5_content.split()
                if parts:
                    local_md5 = parts[0].lower()
                    # Validate hash format
                    if len(local_md5) != 32 or not all(c in "0123456789abcdef" for c in local_md5):
                        logger.debug(f"Invalid local MD5 format for {remote_file}: {local_md5}")
                        files_to_update.add(remote_file)
                        continue
                else:
                    logger.debug(f"Could not parse MD5 for {remote_file}")
                    files_to_update.add(remote_file)
                    continue

                # Compare MD5s
                if local_md5 == remote_md5:
                    logger.debug(f"File unchanged: {remote_file}")
                    continue
                else:
                    logger.debug(
                        f"File changed: {remote_file} (local: {local_md5}, remote: {remote_md5})"
                    )
                    files_to_update.add(remote_file)
            except Exception as e:
                logger.debug(f"Error reading MD5 for {remote_file}: {e}")
                files_to_update.add(remote_file)
        else:
            logger.debug(f"File not in cache: {remote_file}")
            files_to_update.add(remote_file)

    logger.info(f"Found {len(files_to_update)} files that need updating")
    return files_to_update


def create_remote_zip(
    remote_host, remote_user, remote_password, remote_key_file, remote_port, remote_path
):
    """
    Creates a zip archive on the remote server containing specified folders from remote_path.
    Enhanced with incremental change detection and rsync optimization.
    Returns: (zip_path, remote_md5s_dict)
    """
    from .cache_manager import ensure_cache_dirs

    ensure_cache_dirs()
    performance_metrics.start_operation("create_remote_zip")

    logger.info(f"Creating optimized zip archive on {remote_host} from path {remote_path}")

    remote_archive_filename = "cssfdlp_remote_archive.zip"
    base_remote_path = remote_path.rstrip("/")
    remote_archive_full_path = f"{base_remote_path}/{remote_archive_filename}"

    try:
        # Use connection pooling for SSH operations
        ssh_config = {
            "host": remote_host,
            "user": remote_user,
            "password": remote_password,
            "key_file": remote_key_file,
            "port": remote_port,
        }

        with get_ssh_connection(
            remote_host, remote_port, remote_user, remote_password, remote_key_file
        ) as ssh:
            with SSHOperationManager(ssh) as ssh_mgr:
                # Check which folders exist
                actual_folders_to_zip = []
                sftp = ssh_mgr.get_sftp()

                logger.info(
                    f"Checking for allowed folders in {base_remote_path}: {ALLOWED_FASTDL_FOLDERS}"
                )
                for folder_name in ALLOWED_FASTDL_FOLDERS:
                    try:
                        item_path_on_remote = f"{base_remote_path}/{folder_name}"
                        stat_info = sftp.stat(item_path_on_remote)
                        if stat.S_ISDIR(stat_info.st_mode):
                            actual_folders_to_zip.append(folder_name)
                            logger.info(f"Found directory: {folder_name}")
                        else:
                            logger.warning(
                                f"'{folder_name}' in '{base_remote_path}' is a file, not a directory. Skipping."
                            )
                    except FileNotFoundError:
                        logger.warning(
                            f"Folder '{folder_name}' not found in '{base_remote_path}'. Skipping for zip."
                        )

                if not actual_folders_to_zip:
                    error_msg = f"No allowed folders ({', '.join(ALLOWED_FASTDL_FOLDERS)}) found in {base_remote_path}. Cannot create zip."
                    logger.error(error_msg)
                    raise Exception(error_msg)

                logger.info(f"Folders to be included in zip: {', '.join(actual_folders_to_zip)}")

                # Initialize incremental change detection
                cached_zip = get_cached_zip_path(
                    remote_host, remote_path
                )  # cached_processed_dir is used for cache management

                if os.path.exists(cached_zip):
                    source_info = {"path": remote_path, "host": remote_host}
                    get_cached_processed_path_with_fallback(source_info)

                # Set up change detector
                cache_dir = os.path.dirname(cached_zip)
                change_detector = IncrementalChangeDetector(cache_dir)

                # Check for changes using incremental detection
                performance_metrics.start_operation("change_detection")
                should_update, changed_files = change_detector.should_update_remote_zip(
                    ssh_mgr, base_remote_path
                )
                performance_metrics.end_operation(
                    "change_detection", files_checked=len(changed_files) if changed_files else 0
                )

                if not should_update and os.path.exists(cached_zip):
                    logger.info(f"No changes detected, using cached zip: {cached_zip}")
                    # Load cached MD5s
                    remote_md5s = change_detector._load_md5_cache()
                    performance_metrics.end_operation(
                        "create_remote_zip", mode="cached", files=len(remote_md5s)
                    )
                    return cached_zip, remote_md5s

                # Try rsync first for incremental sync
                rsync_manager = RsyncManager(ssh_config)
                zip_creator = IncrementalZipCreator(ssh_mgr)

                if (
                    changed_files is not None
                    and len(changed_files) < 1000
                    and rsync_manager.rsync_available
                ):
                    # Use rsync for small incremental changes
                    performance_metrics.start_operation("rsync_transfer")
                    local_temp_dir = os.path.join(os.path.dirname(cached_zip), "rsync_temp")
                    os.makedirs(local_temp_dir, exist_ok=True)

                    if rsync_manager.sync_from_remote(
                        base_remote_path, local_temp_dir, changed_files
                    ):
                        logger.info("Rsync transfer completed, creating local zip")

                        # Create local zip with synced files
                        local_zip_path = os.path.join(local_temp_dir, remote_archive_filename)
                        if create_local_zip_from_rsync(
                            local_temp_dir, local_zip_path, actual_folders_to_zip
                        ):
                            # Move to cache location
                            import shutil

                            shutil.move(local_zip_path, cached_zip)

                            # Get MD5s for changed files
                            remote_md5s = change_detector.get_incremental_md5s(
                                ssh_mgr, base_remote_path, changed_files
                            )
                            change_detector.update_caches(
                                change_detector.get_remote_file_timestamps(
                                    ssh_mgr, base_remote_path
                                ),
                                remote_md5s,
                            )

                            performance_metrics.end_operation(
                                "rsync_transfer", files=len(changed_files), success=True
                            )
                            performance_metrics.end_operation(
                                "create_remote_zip", mode="rsync", files=len(remote_md5s)
                            )
                            return cached_zip, remote_md5s

                    performance_metrics.end_operation("rsync_transfer", success=False)
                    logger.info("Rsync failed, falling back to zip creation")

                # Fall back to zip creation on remote server
                performance_metrics.start_operation("remote_zip_creation")

                if (
                    changed_files is not None
                    and len(changed_files) < len(change_detector._load_md5_cache()) * 0.5
                ):
                    # Create incremental zip if less than 50% of files changed
                    logger.info(f"Creating incremental zip with {len(changed_files)} changed files")
                    success = zip_creator.create_incremental_zip(
                        base_remote_path, changed_files, remote_archive_full_path
                    )
                else:
                    # Create full zip
                    logger.info("Creating full zip archive")
                    success = zip_creator.create_full_zip(
                        base_remote_path, actual_folders_to_zip, remote_archive_full_path
                    )

                if not success:
                    raise Exception("Failed to create remote zip archive")

                performance_metrics.end_operation(
                    "remote_zip_creation",
                    incremental=changed_files is not None and len(changed_files) < 1000,
                )

                # Get complete MD5s
                performance_metrics.start_operation("md5_calculation")
                remote_md5s = change_detector.get_incremental_md5s(
                    ssh_mgr, base_remote_path, changed_files or set()
                )
                performance_metrics.end_operation("md5_calculation", files=len(remote_md5s))

                # Update caches
                timestamps = change_detector.get_remote_file_timestamps(ssh_mgr, base_remote_path)
                change_detector.update_caches(timestamps, remote_md5s)

                performance_metrics.end_operation(
                    "create_remote_zip", mode="full", files=len(remote_md5s)
                )
                return remote_archive_full_path, remote_md5s

    except Exception as e:
        performance_metrics.end_operation("create_remote_zip", error=str(e))
        logger.error(f"Error creating remote zip: {e}")
        raise


def create_local_zip_from_rsync(source_dir: str, zip_path: str, folders: list) -> bool:
    """Create a local zip file from rsync'd directory structure."""
    try:
        import zipfile

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for folder in folders:
                folder_path = os.path.join(source_dir, folder)
                if os.path.isdir(folder_path):
                    for root, dirs, files in os.walk(folder_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, source_dir)
                            zipf.write(file_path, arcname)
        return True
    except Exception as e:
        logger.error(f"Failed to create local zip: {e}")
        return False


def download_remote_zip(
    remote_host,
    remote_user,
    remote_password,
    remote_key_file,
    remote_port,
    remote_zip_path,
    local_zip_path,
):
    """Download the created zip file from remote server using SFTP with connection pooling."""
    performance_metrics.start_operation("download_remote_zip")
    logger.info(f"Downloading zip file from {remote_host}:{remote_zip_path}")

    try:
        with get_ssh_connection(
            remote_host, remote_port, remote_user, remote_password, remote_key_file
        ) as ssh:
            with SSHOperationManager(ssh) as ssh_mgr:
                sftp = ssh_mgr.get_sftp()  # Get file size for progress tracking
                try:
                    sftp.stat(remote_zip_path)
                    # total_size is used for progress reporting in some cases
                except Exception:
                    pass  # File size not available

                # Progress callback for download
                downloaded_size = [0]  # Use list for mutable closure
                last_logged_percentage = [None]

                def progress_callback(transferred, total):
                    downloaded_size[0] = transferred
                    if total > 0:
                        percentage = (transferred / total) * 100
                        if (
                            last_logged_percentage[0] is None
                            or percentage - last_logged_percentage[0] >= 10
                        ):
                            logger.info(
                                f"Download progress: {percentage:.1f}% ({transferred:,}/{total:,} bytes)"
                            )
                            last_logged_percentage[0] = percentage

                # Download the file
                sftp.get(remote_zip_path, local_zip_path, callback=progress_callback)

                # Verify download
                if os.path.exists(local_zip_path):
                    file_size = os.path.getsize(local_zip_path)
                    logger.info(f"Download completed: {local_zip_path} ({file_size:,} bytes)")
                    performance_metrics.end_operation(
                        "download_remote_zip", size=file_size, success=True
                    )
                else:
                    raise Exception("Downloaded file not found")

    except Exception as e:
        performance_metrics.end_operation("download_remote_zip", error=str(e))
        logger.error(f"Error downloading zip file: {e}")
        raise


def download_zip_from_url(url, local_zip_path):
    """Download a zip file from a URL with progress tracking."""
    logger.info(f"Downloading zip file from URL: {url}")

    try:
        # Start the download with streaming
        response = requests.get(url, stream=True, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()

        # Get total file size from headers
        total_size = int(response.headers.get("content-length", 0))

        # Download with progress tracking
        downloaded_size = 0
        last_logged_percentage = None

        with open(local_zip_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded_size += len(chunk)

                    if total_size > 0:
                        percentage = (downloaded_size / total_size) * 100
                        # Log progress every 10%
                        if (
                            last_logged_percentage is None
                            or percentage - last_logged_percentage >= 10
                        ):
                            logger.info(
                                f"Download progress: {percentage:.1f}% ({downloaded_size:,}/{total_size:,} bytes)"
                            )
                            last_logged_percentage = percentage

        # Verify download
        if os.path.exists(local_zip_path):
            file_size = os.path.getsize(local_zip_path)
            logger.info(f"Download completed: {local_zip_path} ({file_size:,} bytes)")
        else:
            raise Exception("Downloaded file not found")

    except Exception as e:
        logger.error(f"Error downloading from URL: {e}")
        raise


def extract_zip(zip_path, extract_to):
    """Extract a zip file to the specified directory."""
    logger.info(f"Extracting {os.path.basename(zip_path)} to {extract_to}")

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            total_files = len(zip_ref.namelist())
            extracted_count = 0
            last_logged_percentage = None

            for file_info in zip_ref.filelist:
                zip_ref.extract(file_info, extract_to)
                extracted_count += 1

                # Log progress every 10%
                percentage = (extracted_count / total_files) * 100
                if last_logged_percentage is None or percentage - last_logged_percentage >= 10:
                    logger.info(
                        f"Extraction progress: {percentage:.1f}% ({extracted_count}/{total_files} files)"
                    )
                    last_logged_percentage = percentage  # Find the cstrike directory
        logger.info(f"Looking for cstrike directory in: {extract_to}")

        # List all items in the extracted directory for debugging
        all_items = os.listdir(extract_to)
        logger.info(f"Found {len(all_items)} items in extracted directory:")
        for item in all_items:
            item_path = os.path.join(extract_to, item)
            item_type = "dir" if os.path.isdir(item_path) else "file"
            logger.info(f"  {item_type}: {item}")

        cstrike_dir = None
        for item in os.listdir(extract_to):
            item_path = os.path.join(extract_to, item)
            if os.path.isdir(item_path) and item.lower() == "cstrike":
                cstrike_dir = item_path
                break

        if not cstrike_dir:
            logger.info(
                "No 'cstrike' directory found, looking for directories containing allowed folders..."
            )
            # Look for directories containing allowed folders
            for item in os.listdir(extract_to):
                item_path = os.path.join(extract_to, item)
                if os.path.isdir(item_path):
                    # List contents of each directory
                    try:
                        dir_contents = os.listdir(item_path)
                        logger.info(f"Contents of directory '{item}': {dir_contents}")

                        has_allowed_folders = any(
                            os.path.isdir(os.path.join(item_path, folder))
                            for folder in ALLOWED_FASTDL_FOLDERS
                        )
                        logger.info(
                            f"Directory '{item}' has allowed folders: {has_allowed_folders}"
                        )
                        if has_allowed_folders:
                            cstrike_dir = item_path
                            break
                    except PermissionError:
                        logger.warning(f"Permission denied accessing directory: {item}")

        if not cstrike_dir:
            # As a last resort, check if the allowed folders are directly in the extract directory
            logger.info("Checking if allowed folders are directly in the extract directory...")
            has_direct_folders = any(
                os.path.isdir(os.path.join(extract_to, folder)) for folder in ALLOWED_FASTDL_FOLDERS
            )

            if has_direct_folders:
                logger.info("Found allowed folders directly in extract directory")
                cstrike_dir = extract_to
            else:
                logger.error(f"Expected to find one of these folder structures:")
                logger.error(f"1. A directory named 'cstrike'")
                logger.error(f"2. A directory containing any of: {ALLOWED_FASTDL_FOLDERS}")
                logger.error(
                    f"3. The allowed folders directly in the root: {ALLOWED_FASTDL_FOLDERS}"
                )
                raise Exception("No cstrike directory or allowed folders found in extracted files")

        logger.info(f"Found cstrike directory: {cstrike_dir}")
        return cstrike_dir

    except Exception as e:
        logger.error(f"Error extracting zip file: {e}")
        raise
