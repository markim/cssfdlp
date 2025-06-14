#!/usr/bin/env python3
"""
Counter-Strike Source FastDL Processor (cssfdlp.py)

This is the main entry point for the modular cssfdlp toolkit.
The original monolithic script has been broken down into logical modules.
"""

import os
import shutil
import sys
import time
from datetime import datetime

# Third-party libraries - ensure these are in requirements.txt and installed
try:
    from dotenv import load_dotenv
except ImportError as e:
    print(
        f"Error: A required library is missing: {e}. Please install all dependencies from requirements.txt."
    )
    sys.exit(1)

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Import our modules
from src.cache_manager import ensure_cache_dirs  # noqa: E402
from src.cli import parse_arguments  # noqa: E402
from src.config import VERSION  # noqa: E402
from src.config_validator import ConfigValidator, performance_metrics  # noqa: E402
from src.file_utils import validate_all_md5_files_in_directory  # noqa: E402
from src.logger import (  # noqa: E402
    format_time,
    log_error,
    log_performance_summary,
    log_step,
    logger,
)
from src.processor import process_files  # noqa: E402
from src.remote_handler import (  # noqa: E402
    create_remote_zip,
    download_remote_zip,
    download_zip_from_url,
    extract_zip,
)
from src.s3_uploader import upload_to_s3, quick_upload_check  # noqa: E402


def main():
    """Main execution function with enhanced configuration and performance monitoring."""
    # Load environment variables from .env file if it exists
    load_dotenv(override=True)
    start_time = time.time()
    start_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Log a clean header
    log_step(f"COUNTER-STRIKE SOURCE FASTDL PROCESSOR v{VERSION}\nStarted at: {start_datetime}")

    # Parse arguments and validate configuration
    args = parse_arguments()

    # Handle MD5 validation mode
    if args.validate_md5:
        output_dir = args.output_dir or "./processed_cstrike"
        if not os.path.exists(output_dir):
            logger.error(f"Output directory does not exist: {output_dir}")
            sys.exit(1)

        log_step("MD5 VALIDATION MODE")
        logger.info(f"Validating MD5 files in directory: {output_dir}")

        validated_count, fixed_count, error_count = validate_all_md5_files_in_directory(output_dir)

        logger.info(f"MD5 validation completed:")
        logger.info(f"  Files validated: {validated_count}")
        logger.info(f"  MD5 files fixed: {fixed_count}")
        logger.info(f"  Errors: {error_count}")

        if error_count > 0:
            logger.warning(f"Validation completed with {error_count} errors")
            sys.exit(1)
        else:
            logger.info("All MD5 files are now correct")
            sys.exit(0)

    try:
        # Validate runtime requirements
        ConfigValidator.validate_runtime_requirements()

        # Create and validate configuration
        config = ConfigValidator.from_args_and_env(args)
        logger.info("Configuration validated successfully")

    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        sys.exit(1)

    # Initialize performance monitoring
    performance_metrics.start_operation("total_execution")

    # Initialize cache system
    ensure_cache_dirs()

    # Log configuration information in a clean format
    logger.info("CONFIGURATION:")
    logger.info(f"Bucket:               {config.s3.bucket_name}")
    logger.info(
        f"Endpoint URL:         {config.s3.endpoint_url if config.s3.endpoint_url else 'AWS S3 Standard'}"
    )
    logger.info(f"Output Directory:     {config.processing.output_dir}")
    logger.info(f"Skip Upload:          {str(config.processing.skip_upload)}")
    logger.info(f"Keep Temporary Files: {str(config.processing.keep_temp)}")
    logger.info(f"Upload Only Mode:     {str(config.processing.upload_only)}")
    logger.info(f"Parallel Workers:     {config.processing.parallel_workers}")
    logger.info(f"Compression Level:    {config.processing.compression_level}")
    logger.info("Processing folders:   maps, materials, models, sound")

    # Log if AWS credentials are set in environment variables
    if config.s3.access_key_id and config.s3.secret_access_key:
        logger.info("AWS Credentials:      Found in environment variables")
    else:
        logger.info("AWS Credentials:      Using profile configuration")

    # Initialize time variables
    extraction_time = 0
    processing_time = 0
    upload_time = 0
    cleanup_time = 0
    remote_zip_time = 0

    # Define paths for temp and output directories
    temp_dir = os.path.join(os.getcwd(), "temp_extract")

    if config.processing.upload_only:
        # Upload-only mode: skip extraction and processing
        logger.info("Upload-only mode: Skipping extraction and processing steps")

        # Check if the output directory exists and has files
        if not os.path.isdir(args.output_dir):
            logger.error(
                f"Output directory '{args.output_dir}' does not exist or is not a directory"
            )
            sys.exit(1)

        # Count files in output directory
        file_count = sum(len(files) for _, _, files in os.walk(args.output_dir))
        if file_count == 0:
            logger.error(f"Output directory '{args.output_dir}' is empty, no files to upload")
            sys.exit(1)
        # Upload files
        upload_start = time.time()
        log_step("STEP 1/1: UPLOADING TO S3")
        upload_count, error_count = upload_to_s3(
            config.processing.output_dir, config.s3.bucket_name, config.s3.endpoint_url
        )
        upload_time = time.time() - upload_start
    else:
        # Normal mode: perform extraction, processing and upload
        # Implement smart caching - only clean up if necessary
        logger.info(
            "Checking existing cache directories"
        )  # Check if we need to clean temp directory
        if os.path.exists(temp_dir):
            # Only remove temp if it contains old files (older than 1 hour) or if forced
            try:
                temp_age = time.time() - os.path.getctime(temp_dir)
                if temp_age > 3600:  # 1 hour
                    logger.info(f"Removing old temp directory: {temp_dir}")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                else:
                    logger.info(f"Keeping recent temp directory: {temp_dir}")
            except Exception as e:
                logger.info(
                    f"Error checking temp directory age: {e}, removing temp directory: {temp_dir}"
                )
                shutil.rmtree(temp_dir, ignore_errors=True)        # Smart cache handling - only backup files if we actually need to process new ones
        # First, check if we have a recent zip file (meaning no source changes)
        zip_path = config.zip_path
        should_backup_cache = True
        
        if config.create_remote_zip:
            zip_path = os.path.join(temp_dir, "cstrike.zip")
            if os.path.exists(zip_path):
                zip_age = time.time() - os.path.getctime(zip_path)
                if zip_age < 1800:  # 30 minutes - same logic as later in the script
                    should_backup_cache = False  # Using cached zip, so files haven't changed
        
        # For output directory, only backup if we might need to regenerate files
        cached_files = []
        cached_md5_files = []
        upload_state_file = None
        if os.path.exists(config.processing.output_dir):
            logger.info(f"Checking for cached files in: {config.processing.output_dir}")
            for root, _, files in os.walk(config.processing.output_dir):
                for file in files:
                    if file.endswith(".bz2"):
                        cached_files.append(os.path.join(root, file))
                    elif file.endswith(".md5"):
                        cached_md5_files.append(os.path.join(root, file))
                    elif file == ".upload_state.json":
                        upload_state_file = os.path.join(root, file)

            if cached_files and should_backup_cache:
                logger.info(f"Found {len(cached_files)} cached compressed files - backing up for regeneration")
                # Only back up files if we're going to regenerate content
                cache_backup = os.path.join(temp_dir, "cache_backup")
                os.makedirs(cache_backup, exist_ok=True)
                
                # Back up compressed files, MD5 files, and upload state
                all_cached_files = cached_files + cached_md5_files
                if upload_state_file:
                    all_cached_files.append(upload_state_file)
                    
                for cached_file in all_cached_files:
                    rel_path = os.path.relpath(cached_file, config.processing.output_dir)
                    backup_path = os.path.join(cache_backup, rel_path)
                    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                    # Use copy instead of move to preserve the original files
                    shutil.copy2(cached_file, backup_path)  # copy2 preserves metadata
                
                # Only remove the output directory if we have a backup
                logger.info(f"Backed up {len(all_cached_files)} cached files, clearing output directory")
                shutil.rmtree(config.processing.output_dir, ignore_errors=True)
            elif cached_files and not should_backup_cache:
                logger.info(f"Found {len(cached_files)} cached compressed files - preserving them (no source changes detected)")
                # Don't backup or remove anything - files are already where they should be
            else:
                logger.info(
                    f"No cached files found, removing output directory: {config.processing.output_dir}"
                )
                shutil.rmtree(config.processing.output_dir, ignore_errors=True)

        # Create fresh directories
        os.makedirs(temp_dir, exist_ok=True)
        os.makedirs(config.processing.output_dir, exist_ok=True)

        # Determine the zip file path
        zip_path = config.zip_path
        try:  # Determine step sequence based on operation type
            if config.create_remote_zip:
                total_steps = 7
                current_step = 1

                # Check if we already have a recent zip file to avoid re-creation
                zip_path = os.path.join(temp_dir, "cstrike.zip")
                if os.path.exists(zip_path):
                    zip_age = time.time() - os.path.getctime(zip_path)
                    if zip_age < 1800:  # 30 minutes
                        logger.info(
                            f"Using cached zip file: {zip_path} (age: {zip_age/60:.1f} minutes)"
                        )
                        remote_zip_time = 0
                        current_step = 3  # Skip to extraction
                    else:
                        logger.info(
                            f"Cached zip file is too old ({zip_age/60:.1f} minutes), creating new one"
                        )
                        os.remove(zip_path)

                if current_step == 1:
                    log_step(f"STEP {current_step}/{total_steps}: CREATING REMOTE ZIP")
                    remote_zip_creation_start = time.time()

                    remote_zip_path, remote_md5s = create_remote_zip(
                        config.ssh.host,
                        config.ssh.user,
                        config.ssh.password,
                        config.ssh.key_file,
                        config.ssh.port,
                        config.ssh.path,
                    )
                    current_step += 1

                    # Download the created zip file
                    log_step(f"STEP {current_step}/{total_steps}: DOWNLOADING REMOTE ZIP")
                    download_remote_zip(
                        config.ssh.host,
                        config.ssh.user,
                        config.ssh.password,
                        config.ssh.key_file,
                        config.ssh.port,
                        remote_zip_path,
                        zip_path,
                    )
                    current_step += 1

                    remote_zip_time = time.time() - remote_zip_creation_start
                else:
                    # Using cached zip, try to load cached MD5s
                    remote_md5s = None
            elif config.remote_zip_url:
                total_steps = 6
                current_step = 1
                remote_md5s = None  # No remote MD5s available for URL downloads

                # Check if we already have the zip file from URL
                zip_path = os.path.join(temp_dir, "cstrike.zip")
                if os.path.exists(zip_path):
                    zip_age = time.time() - os.path.getctime(zip_path)
                    if zip_age < 1800:  # 30 minutes
                        logger.info(
                            f"Using cached zip file: {zip_path} (age: {zip_age/60:.1f} minutes)"
                        )
                        remote_zip_time = 0
                        current_step = 2  # Skip to extraction
                    else:
                        logger.info(
                            f"Cached zip file is too old ({zip_age/60:.1f} minutes), downloading new one"
                        )
                        os.remove(zip_path)
                if current_step == 1:
                    log_step(f"STEP {current_step}/{total_steps}: DOWNLOADING ZIP FROM URL")
                    remote_zip_creation_start = time.time()

                    download_zip_from_url(config.remote_zip_url, zip_path)
                    current_step += 1

                    remote_zip_time = time.time() - remote_zip_creation_start
            else:
                total_steps = 5
                current_step = 1
                remote_zip_time = 0
                remote_md5s = None  # No remote MD5s available for local files            # Extract the zip file
            extraction_start = time.time()
            
            # Skip extraction and processing if we have cached files and no source changes
            if not should_backup_cache and cached_files:
                logger.info("Skipping extraction and processing - using existing cached files")
                extraction_time = 0
                processing_time = 0
                processed_files = [f for f in cached_files]  # Use existing cached files
                current_step += 2  # Skip extraction and processing steps
            else:
                log_step(f"STEP {current_step}/{total_steps}: EXTRACTING FILES")
                cstrike_dir = extract_zip(zip_path, temp_dir)
                extraction_time = time.time() - extraction_start
                current_step += 1
                # Process files (compress what needs compression)
                processing_start = time.time()
                log_step(f"STEP {current_step}/{total_steps}: PROCESSING FILES")
                processed_files = process_files(
                    cstrike_dir,
                    config.processing.output_dir,
                    remote_md5s,
                    zip_path,
                    config.processing.parallel_workers,
                )            # Restore cached files if they exist
                cache_backup = os.path.join(temp_dir, "cache_backup")
                if os.path.exists(cache_backup):
                    logger.info("Restoring cached compressed files...")
                    restored_count = 0
                    for root, _, files in os.walk(cache_backup):
                        for file in files:
                            if file.endswith(".bz2") or file.endswith(".md5") or file == ".upload_state.json":
                                cached_file = os.path.join(root, file)
                                rel_path = os.path.relpath(cached_file, cache_backup)
                                output_file = os.path.join(config.processing.output_dir, rel_path)

                                # Only restore if we don't have a newer version
                                if not os.path.exists(output_file):
                                    os.makedirs(os.path.dirname(output_file), exist_ok=True)
                                    
                                    # Use copy2 to preserve all metadata including timestamps
                                    shutil.copy2(cached_file, output_file)
                                    
                                    if file.endswith(".bz2"):
                                        processed_files.append(output_file)
                                        restored_count += 1

                    if restored_count > 0:
                        logger.info(f"Restored {restored_count} cached compressed files")

                    # Clean up cache backup
                    shutil.rmtree(cache_backup, ignore_errors=True)
                processing_time = time.time() - processing_start
                current_step += 1

            # Final MD5 validation step
            log_step(f"STEP {current_step}/{total_steps}: VALIDATING MD5 FILES")
            logger.info("Performing final MD5 validation...")
            validated_count, fixed_count, error_count = validate_all_md5_files_in_directory(
                config.processing.output_dir
            )
            logger.info(
                f"MD5 validation: {validated_count} validated, {fixed_count} fixed, {error_count} errors"
            )

            if error_count > 0:
                logger.warning(f"MD5 validation completed with {error_count} errors")
            else:
                logger.info("All MD5 files validated successfully")
            current_step += 1            # Upload to S3 if not skipped
            if not config.processing.skip_upload:
                upload_start = time.time()
                log_step(f"STEP {current_step}/{total_steps}: UPLOADING TO S3")
                  # If we're using cached files and haven't processed anything new, 
                # do a quick check first to avoid unnecessary S3 API calls
                if cached_files and not should_backup_cache and len(processed_files) == len(cached_files):
                    logger.info("Using cached files - performing quick upload check...")
                    if not quick_upload_check(config.processing.output_dir, config.s3.bucket_name, config.s3.endpoint_url):
                        logger.info("Quick check indicates all files are up-to-date - skipping detailed upload check")
                        upload_count, error_count = 0, 0
                        upload_time = time.time() - upload_start
                    else:
                        upload_count, error_count = upload_to_s3(
                            config.processing.output_dir, config.s3.bucket_name, config.s3.endpoint_url
                        )
                        upload_time = time.time() - upload_start
                else:
                    upload_count, error_count = upload_to_s3(
                        config.processing.output_dir, config.s3.bucket_name, config.s3.endpoint_url
                    )
                    upload_time = time.time() - upload_start
                    
                current_step += 1

                if config.s3.endpoint_url:
                    logger.info(
                        f"All files have been uploaded to {config.s3.endpoint_url}/{config.s3.bucket_name}/cstrike/"
                    )
                else:
                    logger.info(
                        f"All files have been uploaded to s3://{config.s3.bucket_name}/cstrike/"
                    )
            else:
                upload_time = 0
                logger.info(
                    f"S3 upload skipped. Processed files are in {config.processing.output_dir}"
                )
        finally:
            # Smart cleanup - preserve cache permanently, only clean temp files
            cleanup_start = time.time()
            log_step(f"STEP {current_step}/{total_steps}: CLEANUP")
            if not config.processing.keep_temp:
                # Only clean temp extraction directory, preserve cache completely
                if os.path.exists(temp_dir):
                    for item in os.listdir(temp_dir):
                        item_path = os.path.join(temp_dir, item)
                        # Skip cache directory if it somehow ended up in temp
                        if item == "cache":
                            continue  # Only clean extracted content, not zip files
                        if os.path.isdir(item_path) and item != "cache":
                            shutil.rmtree(item_path, ignore_errors=True)
                            logger.info(f"Cleaned extracted directory: {item}")

                    # Only remove temp dir if it's empty or contains only zip files
                    remaining_items = [f for f in os.listdir(temp_dir) if f != "cache"]
                    if not remaining_items or all(f.endswith(".zip") for f in remaining_items):
                        # Keep the temp dir structure for future use
                        logger.info(
                            f"Preserved temp directory structure with {len(remaining_items)} cached files"
                        )
                    else:
                        logger.info("Cleaned temporary extraction files")
                else:
                    logger.info("No temporary files to clean")
            else:
                logger.info("Keeping temporary files as requested")
            cleanup_time = time.time() - cleanup_start

    total_time = time.time() - start_time

    # Log a professional summary with performance metrics
    finish_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_step(f"PROCESSING COMPLETED SUCCESSFULLY!\nFinished at: {finish_datetime}")

    # Collect performance metrics from processing
    cached_count = getattr(sys.modules["__main__"], "_cached_count", 0)
    compressed_count = getattr(sys.modules["__main__"], "_compressed_count", 0)
    # copied_count is used in performance summary
    skipped_count = getattr(sys.modules["__main__"], "_skipped_count", 0)
    upload_count = getattr(sys.modules["__main__"], "_upload_count", 0)

    logger.info("TIME STATISTICS:")

    # Only show relevant timing information based on mode
    if not args.upload_only:
        if remote_zip_time > 0:
            if args.create_remote_zip:
                logger.info(f"Remote Zip Creation: {format_time(remote_zip_time)}")
            elif args.remote_zip_url:
                logger.info(f"Remote Zip Download: {format_time(remote_zip_time)}")
        logger.info(f"Extraction Time: {format_time(extraction_time)}")
        logger.info(f"Processing Time: {format_time(processing_time)}")
        if not args.skip_upload:
            logger.info(f"Upload Time: {format_time(upload_time)}")
        logger.info(f"Cleanup Time: {format_time(cleanup_time)}")
    else:
        # In upload-only mode, just show upload time
        logger.info(f"Upload Time: {format_time(upload_time)}")
    logger.info(f"Total Time: {format_time(total_time)}")

    # End performance monitoring
    performance_metrics.end_operation("total_execution")
    # Log performance summary
    log_performance_summary(
        start_time,
        extraction_time,
        processing_time,
        upload_time,
        remote_zip_time,
        cached_count,
        compressed_count,
        skipped_count,
        upload_count,
    )    # Log detailed performance metrics
    performance_metrics.log_summary()


if __name__ == "__main__":
    try:
        # Ensure immediate output flushing
        sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None
        sys.stderr.reconfigure(line_buffering=True) if hasattr(sys.stderr, "reconfigure") else None

        # Set unbuffered output for Python
        os.environ["PYTHONUNBUFFERED"] = "1"

        # Enable ANSI colors on Windows
        if os.name == "nt":
            os.system("color")

        # Execute main function
        main()

    except KeyboardInterrupt:
        log_error("Script interrupted by user (Ctrl+C)")
        sys.exit(130)  # Standard exit code for SIGINT

    except ImportError as e:
        log_error(f"Missing required dependency: {e}")
        log_error("Please run: pip install -r requirements.txt")
        sys.exit(1)

    except FileNotFoundError as e:
        log_error(f"Required file not found: {e}")
        sys.exit(1)

    except PermissionError as e:
        log_error(f"Permission denied: {e}")
        log_error("Please check file permissions or run as administrator")
        sys.exit(1)

    except Exception as e:
        log_error(f"Unexpected error occurred: {str(e)}")
        log_error(f"Error type: {type(e).__name__}")

        # Import traceback for detailed error info
        import traceback

        log_error("Full traceback:")
        for line in traceback.format_exc().split("\n"):
            if line.strip():
                log_error(f"  {line}")
        sys.exit(1)

    finally:
        # Clean up SSH connections
        from src.ssh_manager import ssh_pool

        ssh_pool.close_all()

        # Ensure all output is flushed
        sys.stdout.flush()
        sys.stderr.flush()
