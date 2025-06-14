"""
S3 upload functionality with MD5 comparison and parallel processing.
"""

import concurrent.futures
import json
import os
import time

import boto3
import boto3.s3.transfer as transfer
from botocore.config import Config
from botocore.exceptions import ClientError

from .logger import log_progress_grouped, logger


def test_s3_upload(s3_client):
    """Test S3 connection by attempting to list buckets."""
    try:
        # Test connection by listing buckets
        response = s3_client.list_buckets()
        logger.info("S3 connection test successful")
        logger.debug(f"Found {len(response.get('Buckets', []))} buckets")
        return True
    except Exception as e:
        logger.error(f"S3 connection test failed: {e}")
        return False


def test_s3_compatibility(s3_client, bucket_name):
    """Test S3 endpoint compatibility by uploading a small test file."""
    try:
        test_key = "compatibility_test.txt"
        test_content = b"compatibility test"

        # Try to upload a small test file
        s3_client.put_object(
            Bucket=bucket_name,
            Key=test_key,
            Body=test_content,
            ContentType="text/plain",
        )

        # Clean up test file
        s3_client.delete_object(Bucket=bucket_name, Key=test_key)

        logger.info("S3 endpoint compatibility test passed")
        return True
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "XAmzContentSHA256Mismatch":
            logger.warning("S3 endpoint has SHA256 compatibility issues - will use chunked uploads")
            return "sha256_issues"
        else:
            logger.error(f"S3 compatibility test failed: {error_code} - {str(e)}")
            return False
    except Exception as e:
        logger.error(f"S3 compatibility test failed: {e}")
        return False


def md5_file_needs_upload(local_md5_file, s3_client, bucket_name, s3_key):
    """Check if an MD5 file needs to be uploaded by comparing its content directly."""
    try:
        # Read local MD5 file content
        with open(local_md5_file, "r") as f:
            local_content = f.read().strip().lower()

        # Get remote MD5 file content
        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
            remote_content = response["Body"].read().decode("utf-8").strip().lower()

            # Compare content directly
            return local_content != remote_content
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                # Remote file doesn't exist, needs upload
                return True
            else:
                # Error accessing remote file, assume upload needed
                return True
    except Exception as e:
        logger.debug(f"Error checking MD5 file {local_md5_file}: {e}")
        # Error reading local file, assume upload needed
        return True


def get_remote_md5(s3_client, bucket_name, s3_key):
    """Get MD5 hash of a file from S3 by checking its .md5 file with improved parsing."""
    md5_key = s3_key + ".md5"
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=md5_key)
        md5_content = response["Body"].read().decode("utf-8").strip()

        # Handle different line endings and empty content
        md5_content = md5_content.replace("\r\n", "\n").replace("\r", "\n")
        if not md5_content:
            return None

        # Split on whitespace and take first part as hash
        parts = md5_content.split()
        if not parts:
            return None

        hash_value = parts[0].lower()

        # Validate hash format (32 hex characters for MD5)
        if len(hash_value) != 32 or not all(c in "0123456789abcdef" for c in hash_value):
            logger.warning(f"Invalid MD5 hash format from S3 {md5_key}: {hash_value}")
            return None

        return hash_value
    except ClientError as e:
        # MD5 file doesn't exist
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        logger.error(f"Error getting remote MD5 for {s3_key}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error getting remote MD5 for {s3_key}: {e}")
        return None


def file_needs_upload(local_file, s3_client, bucket_name, s3_key, upload_state=None):
    """Check if a file needs to be uploaded by comparing MD5 hashes and upload state."""
    from .file_utils import calculate_md5

    # First check upload state cache to avoid unnecessary S3 calls
    if upload_state:
        file_key = os.path.basename(local_file)
        if file_key in upload_state:
            try:
                file_stat = os.stat(local_file)
                current_mtime = file_stat.st_mtime
                current_size = file_stat.st_size
                
                cached_mtime = upload_state[file_key].get("mtime", 0)
                cached_size = upload_state[file_key].get("size", 0)
                cached_md5 = upload_state[file_key].get("md5", "").lower()
                cached_uploaded = upload_state[file_key].get("uploaded", False)
                  # If file hasn't changed locally, we have cached info, and it was successfully uploaded before
                if (current_mtime == cached_mtime and current_size == cached_size and 
                    cached_md5 and cached_uploaded):
                    logger.debug(f"File {os.path.basename(local_file)} up-to-date (cached state - no S3 check needed)")
                    return False
                elif not cached_uploaded:
                    logger.debug(f"File {os.path.basename(local_file)} not marked as uploaded in cache")
                elif current_mtime != cached_mtime or current_size != cached_size:
                    logger.debug(f"File {os.path.basename(local_file)} has changed locally since last upload")
                        
            except Exception as e:
                logger.debug(f"Error checking upload state for {local_file}: {e}")

    # Check if we have a local MD5 file for this file
    local_md5_file = local_file + ".md5"

    if os.path.exists(local_md5_file):
        # Read the original file's MD5 from the .md5 file
        try:
            with open(local_md5_file, "r") as f:
                md5_content = f.read().strip()

            # Handle different line endings and parse consistently
            md5_content = md5_content.replace("\r\n", "\n").replace("\r", "\n")
            if not md5_content:
                logger.debug(f"Empty MD5 file {local_md5_file}, calculating from source")
                local_original_md5 = calculate_md5(local_file)
            else:
                # Split on whitespace and take first part as hash
                parts = md5_content.split()
                if parts:
                    local_original_md5 = parts[0].lower()
                    # Validate hash format
                    if len(local_original_md5) != 32 or not all(
                        c in "0123456789abcdef" for c in local_original_md5
                    ):
                        logger.debug(f"Invalid MD5 format in {local_md5_file}, recalculating")
                        local_original_md5 = calculate_md5(local_file)
                else:
                    logger.debug(f"Could not parse MD5 from {local_md5_file}, recalculating")
                    local_original_md5 = calculate_md5(local_file)
        except Exception as e:
            logger.debug(f"Error reading local MD5 file {local_md5_file}: {e}")
            # Fall back to calculating MD5 of the current file
            local_original_md5 = calculate_md5(local_file)
    else:
        # No MD5 file, calculate MD5 of the current file
        local_original_md5 = calculate_md5(local_file)

    if not local_original_md5:
        # Can't determine local MD5, assume upload needed
        return True

    # Ensure local MD5 is lowercase for comparison
    local_original_md5 = local_original_md5.lower()

    remote_md5 = get_remote_md5(s3_client, bucket_name, s3_key)
    if not remote_md5:
        # No remote MD5 file, upload needed
        logger.debug(f"File {os.path.basename(local_file)} needs upload (no remote MD5)")
        return True

    # Compare the original file MD5s (both should be lowercase now)
    needs_upload = local_original_md5 != remote_md5

    if needs_upload:
        logger.debug(f"File {os.path.basename(local_file)} needs upload (MD5 mismatch)")
        logger.debug(f"Local MD5: {local_original_md5}, Remote MD5: {remote_md5}")
    else:
        logger.debug(f"File {os.path.basename(local_file)} up-to-date (MD5 match)")

    return needs_upload


def load_upload_state(processed_dir):
    """Load the upload state cache to track what's already uploaded."""
    state_file = os.path.join(processed_dir, ".upload_state.json")
    try:
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"Error loading upload state: {e}")
    return {}


def save_upload_state(processed_dir, upload_state):
    """Save the upload state cache."""
    state_file = os.path.join(processed_dir, ".upload_state.json")
    try:
        os.makedirs(processed_dir, exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(upload_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save upload state: {e}")


def file_changed_locally(local_file, upload_state):
    """Check if a local file has changed since last upload."""
    try:
        file_stat = os.stat(local_file)
        current_mtime = file_stat.st_mtime
        current_size = file_stat.st_size

        file_key = os.path.basename(local_file)
        if file_key in upload_state:
            cached_mtime = upload_state[file_key].get("mtime", 0)
            cached_size = upload_state[file_key].get("size", 0)

            # File hasn't changed if mtime and size match
            if current_mtime == cached_mtime and current_size == cached_size:
                return False

        return True
    except Exception as e:
        logger.debug(f"Error checking file change for {local_file}: {e}")
        return True  # Assume changed if we can't determine


def upload_to_s3(processed_dir, bucket_name, endpoint_url=None):
    """Upload processed files to S3 with parallel processing and MD5 comparison."""
    logger.info(
        f"Preparing to upload files to S3 bucket: {bucket_name}"
    )  # Configure S3 client with retry logic and optimized settings for Vultr compatibility
    config = Config(
        retries={"max_attempts": 3, "mode": "adaptive"},  # Reduced retries for faster operation
        max_pool_connections=10,  # Reduced connections for better stability
        region_name="us-east-1",  # Default region
        signature_version="s3v4",  # Ensure we use v4 signatures
        s3={
            "addressing_style": "path",  # Use path-style addressing for better compatibility
            "payload_signing_enabled": False,  # Disable payload signing for better compatibility
        },
        read_timeout=300,  # 5 minute read timeout
        connect_timeout=60,  # 1 minute connect timeout
    )
    if endpoint_url:
        s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            config=config,
            use_ssl=True,  # Ensure SSL is used
            verify=True,  # Verify SSL certificates
        )
        logger.info(f"Using custom S3 endpoint: {endpoint_url}")

        # For custom endpoints, disable SSL verification for localhost/development
        if "localhost" in endpoint_url or "127.0.0.1" in endpoint_url:
            logger.warning("Detected localhost endpoint, disabling SSL verification")
            s3_client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                config=config,
                use_ssl=False,
                verify=False,
            )
    else:
        s3_client = boto3.client("s3", config=config)
        logger.info("Using AWS S3 standard endpoint")  # Test S3 connection
    if not test_s3_upload(s3_client):
        logger.error("S3 connection failed. Please check your credentials and endpoint.")
        return 0, 1

    # Test S3 endpoint compatibility
    compatibility_result = test_s3_compatibility(s3_client, bucket_name)
    if compatibility_result is False:
        logger.error("S3 endpoint compatibility test failed.")
        return 0, 1
    elif compatibility_result == "sha256_issues":
        logger.info("S3 endpoint has SHA256 issues - using chunked uploads for all files")
        force_chunked_upload = True
    else:
        force_chunked_upload = False

    # Test S3 compatibility (SHA256 issues)
    compatibility_result = test_s3_compatibility(s3_client, bucket_name)
    if compatibility_result is False:
        logger.error("S3 compatibility test failed. Please check your endpoint configuration.")
        return 0, 1
    elif compatibility_result == "sha256_issues":
        logger.warning(
            "Detected SHA256 compatibility issues with S3 endpoint - chunked uploads will be used"
        )

    # Collect all files to upload
    files_to_upload = []
    for root, dirs, files in os.walk(processed_dir):
        for file in files:
            # Skip upload state cache file - it's only for local caching
            if file == ".upload_state.json":
                continue
                
            local_file_path = os.path.join(root, file)
            # Calculate S3 key (relative path from processed_dir with cstrike prefix)
            relative_path = os.path.relpath(local_file_path, processed_dir)
            s3_key = f"cstrike/{relative_path.replace(os.sep, '/')}"

            files_to_upload.append((local_file_path, s3_key))

    if not files_to_upload:
        logger.warning("No files found to upload")
        return 0, 0

    logger.info(f"Found {len(files_to_upload)} files to check for upload")

    # Load upload state
    upload_state = load_upload_state(processed_dir)  # Filter files that actually need uploading
    files_needing_upload = []
    skipped_count = 0
    last_logged_percentage = None

    for i, (local_file, s3_key) in enumerate(files_to_upload):
        # First check if the file actually needs upload based on content comparison
        if file_needs_upload(local_file, s3_client, bucket_name, s3_key, upload_state):
            files_needing_upload.append((local_file, s3_key))
        else:
            skipped_count += 1

        # Log progress every 10%
        percentage = ((i + 1) / len(files_to_upload)) * 100
        last_logged_percentage = log_progress_grouped(
            percentage,
            i + 1,
            len(files_to_upload),
            f"Checking files (found {len(files_needing_upload)} to upload, {skipped_count} up-to-date)",
            last_logged_percentage,
        )
    if not files_needing_upload:
        logger.info("All files are up-to-date on S3")
        return 0, 0

    logger.info(
        f"Uploading {len(files_needing_upload)} files to S3 (skipped {skipped_count} up-to-date files)"
    )

    # Sort files by size (largest first) for better parallel processing
    def get_file_size(file_tuple):
        try:
            return os.path.getsize(file_tuple[0])
        except Exception:
            return 0

    files_needing_upload.sort(key=get_file_size, reverse=True)

    # Upload files in parallel
    upload_count = 0
    error_count = 0
    last_logged_percentage = None

    def upload_single_file(file_info):
        """Upload a single file to S3 with improved error handling."""
        local_file, s3_key = file_info

        # Validate file before upload
        if not os.path.exists(local_file):
            return False, f"Local file does not exist: {local_file}"

        try:
            file_size = os.path.getsize(local_file)
        except OSError as e:
            return False, f"Cannot access file {local_file}: {str(e)}"
        try:
            # Use chunked upload for all files if compatibility issues detected
            if force_chunked_upload or file_size > 25 * 1024 * 1024:  # 25MB threshold or forced
                return upload_large_file(local_file, s3_key, bucket_name, s3_client)
            else:
                # For smaller files, read the entire file content at once
                try:
                    with open(local_file, "rb") as file_data:
                        file_content = file_data.read()
                except IOError as e:
                    return False, f"Cannot read file {local_file}: {str(e)}"

                # Verify we read the expected amount
                if len(file_content) != file_size:
                    logger.warning(
                        f"File size mismatch for {local_file}: expected {file_size}, read {len(file_content)}"
                    )

                s3_client.put_object(
                    Bucket=bucket_name,
                    Key=s3_key,
                    Body=file_content,
                    ContentLength=len(file_content),
                    ContentType="application/octet-stream",
                )  # Upload MD5 file if it exists
            md5_file = local_file + ".md5"
            if os.path.exists(md5_file):
                md5_s3_key = s3_key + ".md5"
                if md5_file_needs_upload(md5_file, s3_client, bucket_name, md5_s3_key):
                    try:
                        with open(md5_file, "rb") as md5_data:
                            md5_content = md5_data.read()
                    except IOError as e:
                        logger.warning(f"Cannot read MD5 file {md5_file}: {str(e)}")
                        return True, None  # Don't fail the main upload for MD5 issues

                    try:
                        s3_client.put_object(
                            Bucket=bucket_name,
                            Key=md5_s3_key,
                            Body=md5_content,
                            ContentLength=len(md5_content),
                            ContentType="text/plain",
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to upload MD5 file {md5_file}: {str(e)}"
                        )  # Don't fail the main upload for MD5 issues

            return True, None
        except IOError as e:
            return False, f"File I/O error for {local_file}: {str(e)}"
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "XAmzContentSHA256Mismatch":
                # Retry with chunked upload for SHA256 mismatch errors
                logger.warning(
                    f"SHA256 mismatch for {os.path.basename(local_file)}, retrying with chunked upload"
                )
                return upload_large_file(local_file, s3_key, bucket_name, s3_client)
            elif error_code in ["RequestTimeout", "ServiceUnavailable", "SlowDown"]:
                # Retry for transient errors
                logger.warning(
                    f"Transient error for {os.path.basename(local_file)} ({error_code}), retrying with chunked upload"
                )
                return upload_large_file(local_file, s3_key, bucket_name, s3_client)
            else:
                logger.error(
                    f"ClientError uploading {os.path.basename(local_file)}: {error_code} - {str(e)}"
                )
                return False, f"Failed to upload {local_file} to {bucket_name}/{s3_key}: {str(e)}"
        except Exception as e:
            return (
                False,
                f"Failed to upload {local_file} to {bucket_name}/{s3_key}: {str(e)}",
            )  # Use ThreadPoolExecutor for parallel uploads with reduced concurrency

    max_workers = min(5, len(files_needing_upload))  # Max 5 concurrent uploads for stability

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all upload tasks
        future_to_file = {
            executor.submit(upload_single_file, file_info): file_info
            for file_info in files_needing_upload
        }

        # Process completed uploads
        for i, future in enumerate(concurrent.futures.as_completed(future_to_file)):
            file_info = future_to_file[future]
            local_file, s3_key = file_info
            try:
                success, error_msg = future.result()
                if success:
                    upload_count += 1
                    logger.debug(f"Uploaded: {os.path.basename(local_file)}")

                    # Update upload state with MD5 hash for better caching
                    from .file_utils import calculate_md5
                    
                    file_key = os.path.basename(local_file)
                    file_stat = os.stat(local_file)
                    
                    # Get MD5 from the .md5 file if it exists, otherwise calculate it
                    local_md5_file = local_file + ".md5"
                    if os.path.exists(local_md5_file):
                        try:
                            with open(local_md5_file, "r") as f:
                                md5_content = f.read().strip()
                            parts = md5_content.split()
                            file_md5 = parts[0].lower() if parts else calculate_md5(local_file)
                        except Exception:
                            file_md5 = calculate_md5(local_file)
                    else:
                        file_md5 = calculate_md5(local_file)
                    
                    upload_state[file_key] = {
                        "mtime": file_stat.st_mtime,
                        "size": file_stat.st_size,
                        "md5": file_md5 or "",
                        "uploaded": True,  # Mark as successfully uploaded
                    }
                else:
                    error_count += 1
                    logger.error(error_msg)
            except Exception as e:
                error_count += 1
                logger.error(f"Upload failed for {os.path.basename(local_file)}: {e}")

            # Log progress every 10%
            percentage = ((i + 1) / len(files_needing_upload)) * 100
            last_logged_percentage = log_progress_grouped(
                percentage,
                i + 1,
                len(files_needing_upload),
                f"Uploaded {upload_count} files ({error_count} errors)",
                last_logged_percentage,
            )

    # Save upload state
    save_upload_state(processed_dir, upload_state)

    logger.info(
        f"Upload completed: {upload_count} files uploaded, {error_count} errors, {skipped_count} skipped"
    )

    return upload_count, error_count


def upload_large_file(local_file, s3_key, bucket_name, s3_client):
    """Upload large files using multipart upload to avoid SHA256 mismatch issues."""
    try:
        # Create a transfer configuration with more conservative settings for better reliability
        transfer_config = transfer.TransferConfig(
            multipart_threshold=1024 * 25,  # 25MB
            max_concurrency=5,  # Reduced concurrency for stability
            multipart_chunksize=1024 * 25,  # 25MB chunks
            use_threads=True,
            max_io_queue=100,
            io_chunksize=1024 * 256,  # 256KB I/O chunks
        )

        # Create a transfer manager
        transfer_mgr = transfer.create_transfer_manager(s3_client, transfer_config)

        # Upload the file
        transfer_mgr.upload(
            local_file, bucket_name, s3_key, extra_args={"ContentType": "application/octet-stream"}
        )

        return True, None
    except Exception as e:
        return (
            False,
            f"Failed to upload large file {local_file} to {bucket_name}/{s3_key}: {str(e)}",
        )


def quick_upload_check(processed_dir, bucket_name, endpoint_url=None):
    """
    Quickly check if any uploads are needed by checking upload state cache.
    Returns True if uploads are likely needed, False if all files appear up-to-date.
    This is a fast check to avoid unnecessary S3 API calls when using cached files.
    """
    # Load upload state
    upload_state = load_upload_state(processed_dir)
    
    if not upload_state:
        # No upload state cache, uploads likely needed
        return True
    
    # Check if any files have changed since last upload
    files_changed = 0
    files_checked = 0
    
    for root, dirs, files in os.walk(processed_dir):
        for file in files:
            if file == ".upload_state.json":
                continue
                
            local_file_path = os.path.join(root, file)
            file_key = os.path.basename(local_file_path)
            
            files_checked += 1
            
            if file_key not in upload_state:
                files_changed += 1
                continue
            try:
                file_stat = os.stat(local_file_path)
                current_mtime = file_stat.st_mtime
                current_size = file_stat.st_size
                
                cached_mtime = upload_state[file_key].get("mtime", 0)
                cached_size = upload_state[file_key].get("size", 0)
                uploaded = upload_state[file_key].get("uploaded", False)
                  # If file has changed locally OR was never successfully uploaded, uploads needed
                if (current_mtime != cached_mtime or current_size != cached_size or not uploaded):
                    files_changed += 1
                    if not uploaded:
                        logger.debug(f"Quick check: {file_key} not marked as uploaded")
                    elif current_mtime != cached_mtime:
                        logger.debug(f"Quick check: {file_key} mtime changed")
                    elif current_size != cached_size:
                        logger.debug(f"Quick check: {file_key} size changed")
                    
            except Exception:
                files_changed += 1
    
    if files_checked == 0:
        return True  # No files found, something's wrong
        
    # If more than 10% of files have changed, or any files changed, do upload check
    change_ratio = files_changed / files_checked
    
    if files_changed == 0:
        logger.info(f"Quick check: All {files_checked} files appear up-to-date in cache")
        return False
    else:
        logger.info(f"Quick check: {files_changed}/{files_checked} files may need upload")
        return True
