"""
Main processing logic for CS:S files - compression, filtering, and output generation.
Enhanced with parallel processing and performance optimizations.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from .cache_manager import (
    get_cached_processed_path_with_fallback,
    load_remote_md5s,
    store_remote_md5s,
)
from .compression import compress_file, file_needs_compression, should_compress_file
from .config import ALLOWED_FASTDL_FOLDERS
from .config_validator import performance_metrics
from .file_utils import copy_file, ensure_md5_file_correct, read_auto_exclude, should_exclude
from .logger import logger


def process_files(cstrike_dir, output_dir, remote_md5s=None, zip_path=None, max_workers=4):
    """
    Process files from cstrike directory to output directory with compression and filtering.
    Uses intelligent caching to avoid reprocessing unchanged files.
    Enhanced with parallel processing for improved performance.
    """
    performance_metrics.start_operation("file_processing")

    # Validate inputs
    if not cstrike_dir:
        logger.error("cstrike_dir is None or empty")
        performance_metrics.end_operation("file_processing", files_processed=0)
        return []

    if not os.path.exists(cstrike_dir):
        logger.error(f"cstrike_dir does not exist: {cstrike_dir}")
        performance_metrics.end_operation("file_processing", files_processed=0)
        return []

    logger.info(f"Processing files from {cstrike_dir} to {output_dir} with {max_workers} workers")

    # Read auto-exclude patterns
    exclude_patterns = read_auto_exclude(cstrike_dir)
    if exclude_patterns:
        logger.info(f"Using {len(exclude_patterns)} exclude patterns")

    # Get cache directory for this source
    if zip_path:
        source_info = {"zip_path": zip_path, "mtime": os.path.getmtime(zip_path)}
    else:
        source_info = {"cstrike_dir": cstrike_dir}

    cached_processed_dir = get_cached_processed_path_with_fallback(source_info)

    # Store remote MD5s if provided
    if remote_md5s:
        store_remote_md5s(remote_md5s, cached_processed_dir)
    else:
        # Try to load cached remote MD5s
        remote_md5s = load_remote_md5s(cached_processed_dir)

    # Collect all files to process from allowed folders
    performance_metrics.start_operation("file_scanning")
    all_files = []
    for folder in ALLOWED_FASTDL_FOLDERS:
        folder_path = os.path.join(cstrike_dir, folder)
        if os.path.isdir(folder_path):
            logger.info(f"Scanning {folder}/ folder...")
            folder_files = []
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        rel_path = os.path.relpath(file_path, cstrike_dir)
                    except Exception as e:
                        logger.error(
                            f"Error getting relative path for {file_path} from {cstrike_dir}: {e}"
                        )
                        continue

                    # Apply exclusion filters
                    if should_exclude(rel_path, exclude_patterns):
                        continue

                    folder_files.append((file_path, rel_path))

            logger.info(f"Found {len(folder_files)} files in {folder}/ folder")
            all_files.extend(folder_files)

    performance_metrics.end_operation("file_scanning", files_found=len(all_files))

    if not all_files:
        logger.warning("No files found to process")
        performance_metrics.end_operation("file_processing", files_processed=0)
        return []

    logger.info(f"Total files to process: {len(all_files)}")

    # Create output directory structure
    os.makedirs(output_dir, exist_ok=True)

    # Process files in parallel
    performance_metrics.start_operation("parallel_processing")
    processed_files = []

    def process_single_file(file_info: Tuple[str, str]) -> Tuple[bool, str, str]:
        """Process a single file and return (success, source_path, output_path)."""
        source_path, rel_path = file_info

        try:
            # Determine output path
            if should_compress_file(source_path):
                output_path = os.path.join(output_dir, rel_path + ".bz2")
            else:
                output_path = os.path.join(output_dir, rel_path)

            # Create output directory if needed
            if output_path:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Check if file needs processing
            remote_md5 = remote_md5s.get(rel_path) if remote_md5s else None
            if file_needs_compression(source_path, output_path, remote_md5):
                if should_compress_file(source_path):
                    # Compress the file
                    success = compress_file(source_path, output_path, remote_md5)
                    if not success:
                        logger.error(f"Failed to compress {rel_path}")
                        return False, source_path, output_path
                else:
                    # Copy without compression
                    success = copy_file(source_path, output_path, remote_md5)
                    if not success:
                        logger.error(f"Failed to copy {rel_path}")
                        return False, source_path, output_path

                # Ensure MD5 file is correct after processing
                if success and not ensure_md5_file_correct(output_path):
                    logger.warning(f"MD5 file validation failed for {rel_path}")

                return True, source_path, output_path
            else:
                # File already processed and up-to-date, but ensure MD5 file is correct
                if not ensure_md5_file_correct(output_path):
                    logger.warning(f"MD5 file validation failed for existing file {rel_path}")
                return True, source_path, output_path

        except Exception as e:
            logger.error(f"Error processing {rel_path}: {e}")
            return False, source_path, ""

    # Process files in parallel with progress tracking
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_file = {
            executor.submit(process_single_file, file_info): file_info for file_info in all_files
        }

        # Track progress
        completed = 0
        failed = 0

        for future in as_completed(future_to_file):
            file_info = future_to_file[future]
            try:
                success, source_path, output_path = future.result()
                completed += 1

                if success:
                    if output_path:
                        processed_files.append(output_path)
                else:
                    failed += 1

                # Log progress every 100 files or at milestones
                if completed % 100 == 0 or completed in [1, 10, 50] or completed == len(all_files):
                    percentage = (completed / len(all_files)) * 100
                    logger.info(
                        f"Processing progress: {completed}/{len(all_files)} ({percentage:.1f}%) - {failed} failed"
                    )

            except Exception as e:
                failed += 1
                logger.error(f"Task failed for {file_info[1]}: {e}")

    performance_metrics.end_operation(
        "parallel_processing",
        files_processed=len(processed_files),
        files_failed=failed,
        workers=max_workers,
    )

    if failed > 0:
        logger.warning(f"Processing completed with {failed} failures out of {len(all_files)} files")
    else:
        logger.info(f"Processing completed successfully: {len(processed_files)} files processed")

    performance_metrics.end_operation(
        "file_processing",
        total_files=len(all_files),
        processed_files=len(processed_files),
        failed_files=failed,
    )

    return processed_files


def process_files_batch(
    file_batch: List[Tuple[str, str]], output_dir: str, remote_md5s: Dict[str, str] = None
) -> List[str]:
    """Process a batch of files - used for parallel processing."""
    processed_files = []

    for source_path, rel_path in file_batch:
        try:
            # Determine output path
            if should_compress_file(source_path):
                output_path = os.path.join(output_dir, rel_path + ".bz2")
            else:
                output_path = os.path.join(
                    output_dir, rel_path
                )  # Create output directory if needed
            if output_path:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Check if file needs processing
            remote_md5 = remote_md5s.get(rel_path) if remote_md5s else None
            if file_needs_compression(source_path, output_path, remote_md5):
                if should_compress_file(source_path):
                    # Compress the file
                    success = compress_file(source_path, output_path, remote_md5)
                    if success:
                        # Ensure MD5 file is correct
                        ensure_md5_file_correct(output_path)
                        processed_files.append(output_path)
                else:
                    # Copy without compression
                    success = copy_file(source_path, output_path, remote_md5)
                    if success:
                        # Ensure MD5 file is correct
                        ensure_md5_file_correct(output_path)
                        processed_files.append(output_path)
            else:
                # File already processed and up-to-date, but ensure MD5 file is correct
                ensure_md5_file_correct(output_path)
                processed_files.append(output_path)

        except Exception as e:
            logger.error(f"Error processing {rel_path}: {e}")

    return processed_files
