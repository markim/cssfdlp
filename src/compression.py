"""
File compression utilities using bzip2.
"""

import os
import shutil
import subprocess

from .config import COMPRESS_EXTENSIONS
from .file_utils import calculate_md5
from .logger import logger


def compress_file(source_path, output_path, remote_md5=None):
    """
    Compress a file using bzip2 while preserving the original MD5.
    Returns True if successful, False if compression failed.
    """
    try:
        # Calculate MD5 of original file before compression
        original_md5 = calculate_md5(source_path)
        if not original_md5:
            logger.error(f"Could not calculate MD5 for {source_path}")
            return False

        logger.debug(f"Compressing {os.path.basename(source_path)} with bzip2...")

        # Use bzip2 command for compression
        try:
            result = subprocess.run(
                ["bzip2", "-9", "-c", source_path], capture_output=True, check=True, timeout=300
            )

            # Write compressed data to output file
            with open(output_path, "wb") as f:
                f.write(result.stdout)

        except FileNotFoundError:
            logger.error("bzip2 command not found. Please install bzip2.")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"Compression timeout for {source_path}")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Compression failed for {source_path}: {e}")
            return False  # Verify the compressed file was created
        if not os.path.exists(output_path):
            logger.error(f"Compressed file was not created: {output_path}")
            return False  # Create MD5 file for the compressed file (containing original file's MD5)
        md5_file_path = output_path + ".md5"
        try:
            with open(md5_file_path, "w", newline="") as f:
                filename = os.path.basename(source_path)  # Original filename, not compressed
                f.write(f"{original_md5.lower()} *{filename}\n")
            logger.debug(f"Created MD5 file: {md5_file_path}")

            # Verify the MD5 file was created correctly
            try:
                with open(md5_file_path, "r") as f:
                    stored_content = f.read().strip()
                expected_content = f"{original_md5.lower()} *{filename}"
                if stored_content != expected_content:
                    logger.warning(f"MD5 file content mismatch, recreating: {md5_file_path}")
                    with open(md5_file_path, "w", newline="") as f:
                        f.write(f"{expected_content}\n")
            except Exception as e:
                logger.warning(f"Error verifying MD5 file, recreating: {e}")
                with open(md5_file_path, "w", newline="") as f:
                    f.write(f"{original_md5.lower()} *{filename}\n")

        except Exception as e:
            logger.error(f"Error creating MD5 file for {output_path}: {e}")
            # Continue anyway, compression was successful

        # Calculate size reduction
        original_size = os.path.getsize(source_path)
        compressed_size = os.path.getsize(output_path)
        reduction = ((original_size - compressed_size) / original_size) * 100

        logger.debug(
            f"Compression complete: {os.path.basename(source_path)} "
            f"({original_size:,} → {compressed_size:,} bytes, "
            f"{reduction:.1f}% reduction)"
        )

        return True

    except Exception as e:
        logger.error(f"Unexpected error compressing {source_path}: {e}")
        return False


def should_compress_file(file_path):
    """Check if a file should be compressed based on its extension."""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in COMPRESS_EXTENSIONS


def file_needs_compression(source_file, output_file, remote_md5=None):
    """
    Check if a file needs to be processed by comparing with existing output file.
    Returns True if processing is needed, False if output file is up-to-date.
    """  # Check if output file already exists and is up-to-date
    if os.path.exists(output_file):
        try:
            # Compare file modification times
            source_mtime = os.path.getmtime(source_file)
            output_mtime = os.path.getmtime(output_file)

            if output_mtime >= source_mtime:
                # Check MD5 if provided
                if remote_md5:
                    # For compressed files, check if MD5 file exists and matches
                    md5_file = output_file + ".md5"
                    if os.path.exists(md5_file):
                        try:
                            with open(md5_file, "r") as f:
                                stored_md5 = f.read().strip().split()[0].lower()
                            current_md5 = calculate_md5(source_file)
                            if current_md5 and stored_md5 == current_md5.lower():
                                logger.debug(
                                    f"Output file is up-to-date: {os.path.basename(output_file)}"
                                )
                                return False
                        except Exception as e:
                            logger.debug(f"Error checking MD5 for {output_file}: {e}")
                else:
                    logger.debug(f"Output file is up-to-date: {os.path.basename(output_file)}")
                    return False
        except Exception as e:
            logger.debug(f"Error checking file times for {output_file}: {e}")

    return True
