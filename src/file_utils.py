"""
File utilities for MD5 calculation, file comparison, and general file operations.
"""

import hashlib
import os
import shutil

from .logger import logger


def calculate_md5(file_path):
    """Calculate MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"Error calculating MD5 for {file_path}: {e}")
        return None


def create_md5_file(file_path):
    """Create an MD5 file for the given file.
    Uses standardized MD5 format for cross-platform compatibility."""
    md5_hash = calculate_md5(file_path)
    if md5_hash:
        md5_file_path = file_path + ".md5"
        try:
            with open(md5_file_path, "w", newline="") as f:
                # Use Unix line endings and standard format for cross-platform compatibility
                # Format: hash *filename (asterisk indicates binary mode)
                filename = os.path.basename(file_path)
                f.write(f"{md5_hash.lower()} *{filename}\n")
            logger.debug(f"Created MD5 file: {md5_file_path}")
            return md5_file_path
        except Exception as e:
            logger.error(f"Error creating MD5 file for {file_path}: {e}")
            return None
    return None


def verify_md5_file(file_path, md5_file_path):
    """Verify a file against its MD5 file with improved parsing."""
    if not os.path.exists(md5_file_path):
        return False

    try:
        with open(md5_file_path, "r") as f:
            md5_content = f.read().strip()

        # Parse MD5 file format with better compatibility
        # Handle various formats: "hash *filename", "hash  filename", "hash"
        md5_content = md5_content.replace("\r\n", "\n").replace("\r", "\n")
        if not md5_content:
            return False

        # Split on whitespace and take first part as hash
        parts = md5_content.split()
        if not parts:
            return False

        expected_hash = parts[0].lower()

        # Validate hash format (32 hex characters for MD5)
        if len(expected_hash) != 32 or not all(c in "0123456789abcdef" for c in expected_hash):
            logger.warning(f"Invalid MD5 hash format in {md5_file_path}: {expected_hash}")
            return False

        actual_hash = calculate_md5(file_path)

        if actual_hash and expected_hash == actual_hash.lower():
            logger.debug(f"MD5 verification passed for {file_path}")
            return True
        else:
            logger.warning(f"MD5 verification failed for {file_path}")
            logger.debug(
                f"Expected: {expected_hash}, Actual: {actual_hash.lower() if actual_hash else 'None'}"
            )
            return False
    except Exception as e:
        logger.error(f"Error verifying MD5 for {file_path}: {e}")
        return False


def get_file_hash(file_path):
    """Get SHA256 hash of a file for comparison."""
    hash_sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    except Exception:
        return None


def get_file_info(file_path):
    """Get file info (size, mtime, hash) for comparison."""
    try:
        stat = os.stat(file_path)
        return {"size": stat.st_size, "mtime": stat.st_mtime, "hash": get_file_hash(file_path)}
    except Exception:
        return None


def files_are_different(file1, file2):
    """Check if two files are different using size, mtime, and hash."""
    if not os.path.exists(file1) or not os.path.exists(file2):
        return True

    info1 = get_file_info(file1)
    info2 = get_file_info(file2)

    if not info1 or not info2:
        return True

    # Quick check: if sizes are different, files are different
    if info1["size"] != info2["size"]:
        return True

    # If modification times are different, check hash
    if info1["mtime"] != info2["mtime"]:
        return info1["hash"] != info2["hash"]

    # If sizes and mtimes are same, assume files are same
    return False


def copy_with_rsync_logic(source_dir, dest_dir, file_extensions_to_process=None):
    """Copy files from source to destination, only updating changed files."""
    copied_count = 0
    updated_count = 0
    skipped_count = 0

    for root, dirs, files in os.walk(source_dir):
        rel_path = os.path.relpath(root, source_dir)
        dest_root = os.path.join(dest_dir, rel_path) if rel_path != "." else dest_dir

        os.makedirs(dest_root, exist_ok=True)

        for file in files:
            source_file = os.path.join(root, file)
            dest_file = os.path.join(dest_root, file)

            # Check if we should process this file type
            if file_extensions_to_process:
                _, ext = os.path.splitext(file)
                if ext.lower() not in file_extensions_to_process:
                    continue

            if files_are_different(source_file, dest_file):
                shutil.copy2(source_file, dest_file)
                if os.path.exists(dest_file):
                    updated_count += 1
                else:
                    copied_count += 1
            else:
                skipped_count += 1

    return copied_count, updated_count, skipped_count


def read_auto_exclude(cstrike_dir):
    """Read auto-exclude patterns from cstrike directory files."""
    exclude_patterns = []

    # Common exclude files to check
    exclude_files = ["fastdl_exclude.txt", ".fastdlignore", "exclude.txt"]

    for exclude_file in exclude_files:
        exclude_path = os.path.join(cstrike_dir, exclude_file)
        if os.path.exists(exclude_path):
            try:
                with open(exclude_path, "r", encoding="utf-8") as f:
                    patterns = [
                        line.strip() for line in f if line.strip() and not line.startswith("#")
                    ]
                    exclude_patterns.extend(patterns)
                    logger.info(f"Loaded {len(patterns)} exclude patterns from {exclude_file}")
            except Exception as e:
                logger.warning(f"Error reading exclude file {exclude_file}: {e}")

    return exclude_patterns


def should_exclude(file_path, exclude_patterns):
    """Check if a file should be excluded based on patterns."""
    import fnmatch

    if not exclude_patterns:
        return False

    # Normalize path separators for pattern matching
    normalized_path = file_path.replace("\\", "/")

    for pattern in exclude_patterns:
        # Support both glob patterns and simple string matching
        if fnmatch.fnmatch(normalized_path, pattern) or pattern in normalized_path:
            return True

    return False


def copy_file(source_path, output_path, remote_md5=None):
    """
    Copy a file from source to output path and create MD5 file.
    Returns True if successful, False if copy failed.
    """
    try:
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Copy the file
        shutil.copy2(source_path, output_path)

        # Create MD5 file for the copied file
        md5_file_path = create_md5_file(output_path)
        if md5_file_path:
            logger.debug(f"Created MD5 file: {md5_file_path}")
        else:
            logger.warning(f"Failed to create MD5 file for {output_path}")

        logger.debug(f"Copied {os.path.basename(source_path)} to {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error copying {source_path} to {output_path}: {e}")
        return False


def ensure_md5_file_correct(file_path):
    """
    Ensure that the MD5 file for a given file exists and contains the correct hash.
    Creates or updates the MD5 file if necessary.
    Returns True if MD5 file is correct, False if there was an error.
    """
    md5_file_path = file_path + ".md5"

    # Calculate current MD5 of the file
    current_md5 = calculate_md5(file_path)
    if not current_md5:
        logger.error(f"Could not calculate MD5 for {file_path}")
        return False

    # Check if MD5 file exists and is correct
    if os.path.exists(md5_file_path):
        if verify_md5_file(file_path, md5_file_path):
            logger.debug(f"MD5 file is correct: {md5_file_path}")
            return True
        else:
            logger.warning(f"MD5 file is incorrect, regenerating: {md5_file_path}")
    else:
        logger.info(f"MD5 file missing, creating: {md5_file_path}")

    # Create or recreate the MD5 file
    result = create_md5_file(file_path)
    if result:
        logger.debug(f"Successfully ensured correct MD5 file: {md5_file_path}")
        return True
    else:
        logger.error(f"Failed to create MD5 file: {md5_file_path}")
        return False


def validate_all_md5_files_in_directory(directory_path):
    """
    Validate all MD5 files in a directory and its subdirectories.
    Creates missing MD5 files and fixes incorrect ones.
    Returns a tuple of (validated_count, fixed_count, error_count).
    """
    validated_count = 0
    fixed_count = 0
    error_count = 0

    logger.info(f"Validating MD5 files in directory: {directory_path}")

    for root, dirs, files in os.walk(directory_path):
        for file in files:
            # Skip MD5 files themselves
            if file.endswith(".md5"):
                continue

            file_path = os.path.join(root, file)
            md5_file_path = file_path + ".md5"

            try:
                # Check if MD5 file should exist (for cache files we want MD5 files)
                if not os.path.exists(md5_file_path):
                    # Create missing MD5 file
                    if create_md5_file(file_path):
                        fixed_count += 1
                        logger.info(f"Created missing MD5 file: {md5_file_path}")
                    else:
                        error_count += 1
                        logger.error(f"Failed to create MD5 file: {md5_file_path}")
                else:
                    # Validate existing MD5 file
                    if verify_md5_file(file_path, md5_file_path):
                        validated_count += 1
                    else:
                        # Fix incorrect MD5 file
                        if create_md5_file(file_path):
                            fixed_count += 1
                            logger.warning(f"Fixed incorrect MD5 file: {md5_file_path}")
                        else:
                            error_count += 1
                            logger.error(f"Failed to fix MD5 file: {md5_file_path}")

            except Exception as e:
                error_count += 1
                logger.error(f"Error processing MD5 for {file_path}: {e}")

    logger.info(
        f"MD5 validation complete: {validated_count} validated, {fixed_count} fixed, {error_count} errors"
    )
    return validated_count, fixed_count, error_count
