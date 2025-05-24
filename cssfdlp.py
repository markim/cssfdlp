#!/usr/bin/env python3
"""
Counter-Strike Source FastDL Processor (cssfdlp.py)

This script processes a downloaded /cstrike folder by:
1. Unzipping the downloaded archive
2. Compressing maps and audio files using bzip2
3. Uploading the processed files to an S3 bucket, maintaining the directory structure

Only processes and uploads specific folders:
- maps (BSP files)
- materials (textures, shaders)
- models (3D objects)
- sound (audio files)

Requirements:
- Python 3.6+
- boto3 (AWS SDK for Python)
- bzip2 command-line tool installed on the system
- python-dotenv (for environment variable support)
"""

import os
import sys
import zipfile
import shutil
import glob
import subprocess
import logging
import boto3
import botocore
from botocore.exceptions import ClientError
import argparse
from pathlib import Path
from dotenv import load_dotenv
import time
from datetime import datetime
from botocore.config import Config

# Load environment variables from .env file if it exists
load_dotenv()

# Configure logging for file output
file_handler = logging.FileHandler("cssfdlp.log")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Configure simple console handler
class SimpleConsoleHandler(logging.StreamHandler):
    def emit(self, record):
        # Save original message
        orig_msg = record.msg
        
        # Add prefix based on log level
        if record.levelno >= logging.ERROR:
            record.msg = f"ERROR: {record.msg}"
        elif record.levelno >= logging.WARNING:
            record.msg = f"WARNING: {record.msg}"
        elif record.levelno >= logging.INFO:
            # Different prefixes for different types of info messages
            if "Processing" in record.msg or "Completed" in record.msg or "Success" in record.msg:
                record.msg = f"INFO: {record.msg}"
            elif "Uploading" in record.msg:
                record.msg = f"UPLOAD: {record.msg}"
            elif "Compressing" in record.msg:
                record.msg = f"COMPRESS: {record.msg}"
            elif "Extracting" in record.msg or "Copying" in record.msg:
                record.msg = f"FILE: {record.msg}"
            else:
                record.msg = f"INFO: {record.msg}"
        
        # Call the original handler
        super().emit(record)
        
        # Restore original message for file logging
        record.msg = orig_msg

console_handler = SimpleConsoleHandler()
        
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

# Function to format time duration
def format_time(seconds):
    if seconds < 60:
        return f"{seconds:.2f} seconds"
    elif seconds < 3600:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        return f"{int(minutes)} minutes {int(remaining_seconds)} seconds"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        remaining_seconds = seconds % 60
        return f"{int(hours)} hours {int(minutes)} minutes {int(remaining_seconds)} seconds"

# File extensions that should be compressed with bzip2
COMPRESS_EXTENSIONS = [
    '.bsp',    # Maps
    '.nav',    # Navigation meshes
    '.ain',    # AI nodes
    '.wav',    # Audio files
    # '.mp3',  # MP3s already compressed, minimal benefit per Valve wiki
    '.ogg'     # Audio files
]

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Process CS:S files for FastDL server.')
    parser.add_argument('zip_path', help='Path to the downloaded cstrike zip file', nargs='?')
    parser.add_argument('--bucket', default=os.environ.get('AWS_BUCKET_NAME'), 
                        help='S3 bucket name (env: AWS_BUCKET_NAME)')
    parser.add_argument('--endpoint-url', default=os.environ.get('AWS_ENDPOINT_URL'), 
                        help='S3 endpoint URL (env: AWS_ENDPOINT_URL)')
    parser.add_argument('--output-dir', default=os.environ.get('OUTPUT_DIR', './processed_cstrike'), 
                        help='Directory to store processed files (env: OUTPUT_DIR)')
    parser.add_argument('--skip-upload', action='store_true', default=os.environ.get('SKIP_UPLOAD', '').lower() in ('true', 'yes', '1'),
                        help='Skip uploading to S3 (env: SKIP_UPLOAD)')
    parser.add_argument('--keep-temp', action='store_true', default=os.environ.get('KEEP_TEMP', '').lower() in ('true', 'yes', '1'),
                        help='Keep temporary files after processing (env: KEEP_TEMP)')
    parser.add_argument('--upload-only', action='store_true', default=os.environ.get('UPLOAD_ONLY', '').lower() in ('true', 'yes', '1'),
                        help='Skip extraction and processing, only upload files from output directory (env: UPLOAD_ONLY)')
    args = parser.parse_args()
    
    # Validate required parameters
    if not args.bucket:
        parser.error("S3 bucket name is required. Provide it via --bucket parameter or AWS_BUCKET_NAME environment variable.")
    
    # If upload-only is specified, we don't need a zip file
    if not args.upload_only and not args.zip_path:
        parser.error("Zip file path is required unless --upload-only is specified")
    
    # If upload-only is specified, check if output directory exists
    if args.upload_only:
        if not os.path.exists(args.output_dir):
            parser.error(f"Output directory '{args.output_dir}' does not exist. Cannot use --upload-only.")
        # Skip upload cannot be used with upload only
        if args.skip_upload:
            parser.error("Cannot use --upload-only and --skip-upload together.")
    
    return args

def extract_zip(zip_path, extract_to):
    """Extract only allowed folders from the zip file to the specified directory."""
    logger.info(f"Extracting {zip_path} to {extract_to}")
    try:
        # Get zip file size for progress calculation
        zip_size = os.path.getsize(zip_path)
        if zip_size < 1024 * 1024:
            formatted_size = f"{zip_size / 1024:.2f} KB"
        elif zip_size < 1024 * 1024 * 1024:
            formatted_size = f"{zip_size / (1024 * 1024):.2f} MB"
        else:
            formatted_size = f"{zip_size / (1024 * 1024 * 1024):.2f} GB"
            
        print(f"Starting extraction of {os.path.basename(zip_path)} ({formatted_size})")
        
        # Only extract these specific folders
        allowed_folders = ['maps', 'materials', 'models', 'sound']
        logger.info(f"Only extracting these folders: {', '.join(allowed_folders)}")
        
        # Custom extraction with simple progress indicator
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Get all files in the zip
            all_files = zip_ref.namelist()
            
            # Filter files to only include allowed folders
            files_to_extract = []
            for file in all_files:
                # Check if the file is in a cstrike subfolder
                if 'cstrike/' in file:
                    parts = file.split('cstrike/', 1)[1].split('/', 1)
                    top_folder = parts[0] if len(parts) > 0 else ''
                    if top_folder in allowed_folders:
                        files_to_extract.append(file)
                else:
                    # If there's no cstrike/ prefix, check the top folder directly
                    parts = file.split('/', 1)
                    top_folder = parts[0] if len(parts) > 0 else ''
                    if top_folder in allowed_folders:
                        files_to_extract.append(file)
            
            total_files = len(files_to_extract)
            print(f"Found {total_files} files in allowed folders to extract")
            
            # Extract filtered files with progress indication
            for i, file in enumerate(files_to_extract, 1):
                # Print progress every 100 files or at specific percentages
                if i % 100 == 0 or i == 1 or i == total_files or i == total_files // 2:
                    percent = round((i / total_files) * 100, 1)
                    print(f"Extracting: {percent}% ({i}/{total_files})")
                
                # Extract the file
                zip_ref.extract(file, extract_to)
        
        logger.info("Extraction completed successfully")
        
        # Check if 'cstrike' is in extracted contents
        extracted_paths = os.listdir(extract_to)
        
        if 'cstrike' in extracted_paths:
            print("Found 'cstrike' directory in extracted contents")
            logger.info("Found 'cstrike' directory in extracted contents")
            return os.path.join(extract_to, 'cstrike')
        else:
            print("Assuming extracted content is already the cstrike folder")
            logger.info("Assuming extracted content is already the cstrike folder")
            return extract_to
            
    except zipfile.BadZipFile:
        print(f"ERROR: The file {zip_path} is not a valid zip file.")
        logger.error(f"The file {zip_path} is not a valid zip file.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {str(e)}")
        logger.error(f"Error extracting zip file: {e}")
        sys.exit(1)

def compress_file(file_path):
    """Compress a file using bzip2 and return the compressed file path. 
    The original file is preserved using the -k flag."""
    compressed_path = file_path + '.bz2'
    
    try:
        # Get original file size
        original_size = os.path.getsize(file_path)
        
        # Compress the file
        start_time = time.time()
        result = subprocess.run(['bzip2', '-zk', file_path], capture_output=True, text=True, check=True)
        compress_time = time.time() - start_time
        
        # Get compressed file size and calculate ratio
        if os.path.exists(compressed_path):
            compressed_size = os.path.getsize(compressed_path)
            ratio = (1 - (compressed_size / original_size)) * 100 if original_size > 0 else 0
            
            # Log compression details
            if original_size < 1024 * 1024:
                orig_str = f"{original_size / 1024:.2f} KB"
                comp_str = f"{compressed_size / 1024:.2f} KB"
            else:
                orig_str = f"{original_size / (1024 * 1024):.2f} MB"
                comp_str = f"{compressed_size / (1024 * 1024):.2f} MB"
            
            logger.info(f"Compressed {os.path.basename(file_path)}: {orig_str} → {comp_str} ({ratio:.1f}% saved) in {compress_time:.2f}s")
            
        return compressed_path
    except subprocess.CalledProcessError as e:
        logger.error(f"bzip2 compression failed for {file_path}: {e.stderr}")
        print(f"Compression failed for {os.path.basename(file_path)}: {e.stderr}")
        return None
    except FileNotFoundError:
        error_msg = "bzip2 command not found. Please install bzip2."
        logger.error(error_msg)
        print(f"ERROR: {error_msg}")
        sys.exit(1)

def read_auto_exclude(cstrike_dir):
    """
    Read the .autoExclude file if it exists and return a list of files/patterns to exclude.
    """
    exclude_path = os.path.join(cstrike_dir, '.autoExclude')
    exclude_list = []
    
    if os.path.exists(exclude_path):
        try:
            with open(exclude_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        exclude_list.append(line)
            logger.info(f"Loaded {len(exclude_list)} exclusion patterns from .autoExclude")
            print(f"Loaded {len(exclude_list)} exclusion patterns from .autoExclude")
        except Exception as e:
            logger.error(f"Error reading .autoExclude file: {e}")
            print(f"Error reading .autoExclude file: {e}")
    else:
        logger.info("No .autoExclude file found, proceeding without exclusions")
        print(f"No .autoExclude file found, proceeding without exclusions")
    
    return exclude_list

def should_exclude(file_path, exclude_patterns):
    """
    Check if a file should be excluded based on the exclusion patterns.
    """
    if not exclude_patterns:
        return False
        
    file_name = os.path.basename(file_path)
    
    for pattern in exclude_patterns:
        # Exact match
        if file_name == pattern:
            return True
        
        # Simple wildcard pattern (*.ext)
        if pattern.startswith('*') and file_name.endswith(pattern[1:]):
            return True
    
    return False

def process_files(cstrike_dir, output_dir):
    """Process files by compressing those that need compression and copying others.
    Only processes specific folders: maps, materials, resource, and sound."""
    logger.info(f"Processing files from {cstrike_dir} to {output_dir}")
    
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    processed_files = []
    compressed_count = 0
    copied_count = 0
    
    # Only process these specific folders
    allowed_folders = ['maps', 'materials', 'models', 'sound']
    logger.info(f"Only processing these folders: {', '.join(allowed_folders)}")
    
    # Read exclude patterns from .autoExclude file
    exclude_patterns = read_auto_exclude(cstrike_dir)
    logger.info(f"Excluding files matching these patterns: {', '.join(exclude_patterns)}")
    
    # First count total files to process for progress reporting
    total_files = 0
    for root, _, files in os.walk(cstrike_dir):
        rel_path = os.path.relpath(root, cstrike_dir)
        top_folder = rel_path.split(os.sep)[0] if os.sep in rel_path else rel_path
        if top_folder == '.' or top_folder in allowed_folders:
            total_files += len(files)
    
    print(f"Found {total_files} files to process in allowed folders")
    processed_count = 0
    
    # Walk through all files in the cstrike directory
    for root, dirs, files in os.walk(cstrike_dir):
        # Create relative path to maintain directory structure
        rel_path = os.path.relpath(root, cstrike_dir)
        
        # Skip folders that are not in the allowed list
        # Check if the top-level folder is in the allowed list
        top_folder = rel_path.split(os.sep)[0] if os.sep in rel_path else rel_path
        if top_folder != '.' and top_folder not in allowed_folders:
            continue
        
        output_path = os.path.join(output_dir, rel_path)
        
        # Ensure the output directory exists
        os.makedirs(output_path, exist_ok=True)
        
        for file in files:
            # Check if the file matches any exclude pattern
            if should_exclude(file, exclude_patterns):
                logger.info(f"Skipping excluded file: {file}")
                continue
            
            processed_count += 1
            
            # Print progress every 50 files or at specific milestones
            if processed_count % 50 == 0 or processed_count == 1 or processed_count == total_files or processed_count == total_files // 2:
                percent = round((processed_count / total_files) * 100, 1)
                print(f"Processing: {percent}% ({processed_count}/{total_files})")
            
            source_file = os.path.join(root, file)
            output_file = os.path.join(output_path, file)
            
            # Check if the file should be compressed
            _, ext = os.path.splitext(file)
            if ext.lower() in COMPRESS_EXTENSIONS:
                # Compress the file
                compressed_file = compress_file(source_file)
                if compressed_file:
                    # The compressed file will be in the same directory as the source
                    # Move it to output directory with preserved structure
                    compressed_name = os.path.basename(compressed_file)
                    output_compressed = os.path.join(output_path, compressed_name)
                    shutil.move(compressed_file, output_compressed)
                    processed_files.append(output_compressed)
                    compressed_count += 1
                    
                    # Also copy the original file to the output directory 
                    shutil.copy2(source_file, output_file)
                    processed_files.append(output_file)
                    copied_count += 1
            else:
                # Copy the file without compression
                shutil.copy2(source_file, output_file)
                processed_files.append(output_file)
                copied_count += 1
    
    # Print summary
    print("\nProcessing Summary:")
    print(f"Files Compressed: {compressed_count}")
    print(f"Files Copied: {copied_count}")
    print(f"Total Processed: {len(processed_files)}")
    print(f"Note: Both original and compressed versions of .bsp files are included")
    
    logger.info(f"Processed {len(processed_files)} files ({compressed_count} compressed, {copied_count} copied)")
    logger.info(f"Both original and compressed versions of .bsp files are included in the output")
    return processed_files

def upload_to_s3(processed_dir, bucket_name, endpoint_url=None):
    """Upload processed files to S3 bucket, maintaining directory structure."""
    logger.info(f"Uploading files to S3 bucket: {bucket_name}")
    
    # Get AWS credentials from environment variables
    aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    # Log basic connection information
    logger.info(f"Preparing to upload to bucket: {bucket_name}")
    if endpoint_url:
        logger.info(f"Using S3-compatible storage endpoint: {endpoint_url}")
        
        # Strip https:// if present in the endpoint_url
        if endpoint_url.startswith('https://'):
            endpoint_url = endpoint_url[8:]
            print(f"Simplified endpoint URL to: {endpoint_url}")
        elif endpoint_url.startswith('http://'):
            endpoint_url = endpoint_url[7:]
            print(f"Simplified endpoint URL to: {endpoint_url}")
            
    # Define test upload function
    def test_s3_upload(s3_client):
        logger.info("Testing S3 connection with a test upload...")
        test_file_path = os.path.join(processed_dir, "test_upload.txt")
        try:
            # Create a small test file
            with open(test_file_path, 'w') as f:
                f.write("This is a test file to verify S3 upload functionality.")
                  
            test_key = "cstrike/test_upload.txt"
            
            # Upload the test file with public-read ACL
            s3_client.upload_file(
                test_file_path,
                bucket_name, 
                test_key,
                ExtraArgs={'ACL': 'public-read'}
            )
            logger.info("Test upload successful")
            
            # Try to delete the test file from S3
            s3_client.delete_object(Bucket=bucket_name, Key=test_key)
            
            # Remove local test file
            os.remove(test_file_path)
            return True
        except Exception as e:
            print(f"Test upload failed: {str(e)}")
            logger.error(f"Test upload failed: {str(e)}")
            return False
    
    try:
        session = boto3.session.Session()
        
        if aws_access_key and aws_secret_key:
            # Using explicit credentials
            if endpoint_url:  # S3-compatible storage configuration
                region_name = endpoint_url.split('.')[0]
                s3_client = session.client(
                    's3',
                    endpoint_url="https://" + endpoint_url,
                    aws_access_key_id=aws_access_key,
                    aws_secret_access_key=aws_secret_key,
                    region_name=region_name,
                    config=Config(
                        request_checksum_calculation="when_required",
                        response_checksum_validation="when_required",
                        s3={
                            "addressing_style":'virtual'
                        }
                    )
                )
                logger.info("Using environment credentials with custom endpoint")
            else:
                # Standard AWS S3 client
                s3_client = session.client(
                    's3',
                    aws_access_key_id=aws_access_key,
                    aws_secret_access_key=aws_secret_key
                )
                logger.info("Using environment credentials with standard AWS S3")
        else:
            # Using profile credentials
            if endpoint_url:
                region_name = endpoint_url.split('.')[0]
                s3_client = session.client(
                    's3', 
                    endpoint_url="https://" + endpoint_url,
                    region_name=region_name,
                    config=Config(
                        addressing_style='virtual',
                        request_checksum_calculation="when_required",
                        response_checksum_validation="when_required"
                    )
                )
                logger.info("Using profile credentials with custom endpoint")
            else:
                s3_client = session.client('s3')
                logger.info("Using profile credentials with standard AWS S3")
        
        # Test connection
        logger.info(f"Verifying bucket '{bucket_name}' exists...")
        try:
            print(f"Bucket '{bucket_name}' exists and is accessible")
            
            # Test upload with a small file
            if not test_s3_upload(s3_client):
                print(f"Test upload failed, aborting full upload process")
                logger.error("Test upload failed, aborting full upload process")
                return 0, 1
                
        except Exception as e:
            print(f"ERROR: Could not access bucket '{bucket_name}': {str(e)}")
            logger.error(f"Could not access bucket '{bucket_name}': {str(e)}")
            return 0, 1
            
    except Exception as e:
        error_msg = f"Failed to initialize S3 client: {str(e)}"
        print(f"ERROR: {error_msg}")
        logger.error(error_msg)
        return 0, 1
    
    # First count all files to be uploaded for progress tracking
    total_files = sum(len(files) for _, _, files in os.walk(processed_dir))
    print(f"Found {total_files} files to upload")
    
    upload_count = 0
    error_count = 0
    
    start_time = time.time()
    total_size = 0
    
    # Calculate total size for better progress reporting
    for root, _, files in os.walk(processed_dir):
        for file in files:
            local_file = os.path.join(root, file)
            total_size += os.path.getsize(local_file)
    
    # Format total size
    if total_size < 1024 * 1024:
        formatted_total = f"{total_size / 1024:.2f} KB"
    elif total_size < 1024 * 1024 * 1024:
        formatted_total = f"{total_size / (1024 * 1024):.2f} MB"
    else:
        formatted_total = f"{total_size / (1024 * 1024 * 1024):.2f} GB"
    
    print(f"Total upload size: {formatted_total}")
    
    # Files to upload
    files_to_upload = []
    for root, _, files in os.walk(processed_dir):
        for file in files:
            local_file = os.path.join(root, file)
            rel_path = os.path.relpath(root, processed_dir)
            
            if rel_path == '.':
                s3_key = file
            else:
                s3_key = os.path.join(rel_path, file).replace('\\', '/')
            
            # Ensure the S3 key starts with "cstrike/" for proper structure
            if not s3_key.startswith('cstrike/'):
                s3_key = f"cstrike/{s3_key}"
                
            files_to_upload.append((local_file, s3_key))
    
    # Sort files: compressed files (maps, sounds) first, then other files
    files_to_upload.sort(key=lambda x: not x[0].endswith('.bz2'))
    
    # Upload files
    for idx, (local_file, s3_key) in enumerate(files_to_upload, 1):
        # Print progress every 20 files or at specific milestones
        if idx % 20 == 0 or idx == 1 or idx == total_files or idx == total_files // 2:
            percent = round((idx / total_files) * 100, 1)
            
            # Calculate upload speed
            current_size = sum(os.path.getsize(f[0]) for f in files_to_upload[:idx])
            elapsed_time = time.time() - start_time
            
            if elapsed_time > 0:
                upload_speed = current_size / elapsed_time / 1024  # KB/s
                
                # Format upload speed
                if upload_speed < 1024:
                    speed_str = f"{upload_speed:.2f} KB/s"
                else:
                    speed_str = f"{upload_speed / 1024:.2f} MB/s"
            else:
                speed_str = "Calculating..."
                
            print(f"Uploading: {percent}% ({idx}/{total_files}) - {speed_str} - {os.path.basename(local_file)}")
        
        try:
            # Process the upload
            start_upload = time.time()
            
            # Upload the file without too many logs
            # Set ACL to 'public-read' to make the files publicly accessible
            s3_client.upload_file(
                local_file,
                bucket_name,
                s3_key,
                ExtraArgs={'ACL': 'public-read'}
            )
            
            upload_duration = time.time() - start_upload
            logger.info(f"Uploaded {os.path.basename(local_file)}")
            upload_count += 1
        except ClientError as e:
            error_msg = f"Error uploading {os.path.basename(local_file)}: {str(e)}"
            print(f"ERROR: {error_msg}")
            logger.error(error_msg)
            error_count += 1
        except Exception as e:
            error_msg = f"Error uploading {os.path.basename(local_file)}: {str(e)}"
            print(f"ERROR: {error_msg}")
            logger.error(error_msg)
            error_count += 1
    
    # Calculate final stats
    final_time = time.time() - start_time
    if final_time > 0:
        avg_speed = total_size / final_time / 1024  # KB/s
        
        # Format average speed
        if avg_speed < 1024:
            avg_speed_str = f"{avg_speed:.2f} KB/s"
        else:
            avg_speed_str = f"{avg_speed / 1024:.2f} MB/s"
    else:
        avg_speed_str = "N/A"
    
    # Print upload summary
    print("\nUpload Summary:")
    print(f"Files Uploaded: {upload_count}")
    print(f"Upload Errors: {error_count}")
    print(f"Total Size: {formatted_total}")
    print(f"Average Speed: {avg_speed_str}")
    print(f"Upload Time: {format_time(final_time)}")
    
    logger.info(f"Upload complete: {upload_count} files uploaded, {error_count} errors")
    return upload_count, error_count

def main():
    """Main execution function."""
    start_time = time.time()
    start_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Print a clean header
    print("\n" + "=" * 60)
    print("  COUNTER-STRIKE SOURCE FASTDL PROCESSOR")
    print(f"  Started at: {start_datetime}")
    print("=" * 60 + "\n")
    
    args = parse_arguments()
    
    # Log configuration information in a clean format
    print("CONFIGURATION:")
    print("-" * 60)
    print(f"Bucket:               {args.bucket}")
    print(f"Endpoint URL:         {args.endpoint_url if args.endpoint_url else 'AWS S3 Standard'}")
    print(f"Output Directory:     {args.output_dir}")
    print(f"Skip Upload:          {str(args.skip_upload)}")
    print(f"Keep Temporary Files: {str(args.keep_temp)}")
    print(f"Upload Only Mode:     {str(args.upload_only)}")
    print(f"Processing folders:   maps, materials, models, sound")
    
    # Log if AWS credentials are set in environment variables
    if os.environ.get('AWS_ACCESS_KEY_ID') and os.environ.get('AWS_SECRET_ACCESS_KEY'):
        print(f"AWS Credentials:      Found in environment variables")
    else:
        print(f"AWS Credentials:      Using profile configuration")
    print("-" * 60 + "\n")
    
    # Initialize time variables
    extraction_time = 0
    processing_time = 0
    upload_time = 0
    cleanup_time = 0
    
    # Define paths for temp and output directories
    temp_dir = os.path.join(os.getcwd(), "temp_extract")
    
    if args.upload_only:
        # Upload-only mode: skip extraction and processing
        logger.info("Upload-only mode: Skipping extraction and processing steps")
        print("Upload-only mode: Skipping extraction and processing steps")
        
        # Check if the output directory exists and has files
        if not os.path.isdir(args.output_dir):
            logger.error(f"Output directory '{args.output_dir}' does not exist or is not a directory")
            print(f"ERROR: Output directory '{args.output_dir}' does not exist or is not a directory")
            sys.exit(1)
            
        # Count files in output directory
        file_count = sum(len(files) for _, _, files in os.walk(args.output_dir))
        if file_count == 0:
            logger.error(f"Output directory '{args.output_dir}' is empty, no files to upload")
            print(f"ERROR: Output directory '{args.output_dir}' is empty, no files to upload")
            sys.exit(1)
        
        # Upload files
        upload_start = time.time()
        print("\n[STEP 1/1] UPLOADING TO S3...")
        upload_count, error_count = upload_to_s3(args.output_dir, args.bucket, args.endpoint_url)
        upload_time = time.time() - upload_start
    else:
        # Normal mode: perform extraction, processing and upload
        # Clean up existing directories if they exist
        logger.info("Cleaning up existing directories before processing")
        if os.path.exists(temp_dir):
            print(f"Removing existing temp directory: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            
        if os.path.exists(args.output_dir):
            print(f"Removing existing output directory: {args.output_dir}")
            shutil.rmtree(args.output_dir, ignore_errors=True)
            
        # Create fresh directories
        os.makedirs(temp_dir, exist_ok=True)
        os.makedirs(args.output_dir, exist_ok=True)
        
        try:
            # 2. Extract the zip file
            extraction_start = time.time()
            print("[STEP 1/4] EXTRACTING FILES...")
            cstrike_dir = extract_zip(args.zip_path, temp_dir)
            extraction_time = time.time() - extraction_start
            
            # 3. Process files (compress what needs compression)
            processing_start = time.time()
            print("\n[STEP 2/4] PROCESSING FILES...")
            processed_files = process_files(cstrike_dir, args.output_dir)
            processing_time = time.time() - processing_start
            
            # 4. Upload to S3 if not skipped
            if not args.skip_upload:
                upload_start = time.time()
                print("\n[STEP 3/4] UPLOADING TO S3...")
                upload_count, error_count = upload_to_s3(args.output_dir, args.bucket, args.endpoint_url)
                upload_time = time.time() - upload_start
                
                if args.endpoint_url:
                    logger.info(f"All files have been uploaded to {args.endpoint_url}/{args.bucket}/cstrike/")
                    print(f"All files have been uploaded to {args.endpoint_url}/{args.bucket}/cstrike/")
                else:
                    logger.info(f"All files have been uploaded to s3://{args.bucket}/cstrike/")
                    print(f"All files have been uploaded to s3://{args.bucket}/cstrike/")
            else:
                upload_time = 0
                logger.info(f"S3 upload skipped. Processed files are in {args.output_dir}")
            
        finally:
            # 5. Clean up temporary files if requested
            cleanup_start = time.time()
            print("\n[STEP 4/4] CLEANUP...")
            if not args.keep_temp:
                logger.info(f"Cleaning up temporary directory {temp_dir}")
                shutil.rmtree(temp_dir, ignore_errors=True)
            cleanup_time = time.time() - cleanup_start
    
    total_time = time.time() - start_time
    
    # Print a professional summary
    print("\n" + "=" * 60)
    print("  PROCESSING COMPLETED SUCCESSFULLY!")
    print(f"  Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print("\nTIME STATISTICS:")
    print("-" * 60)
    
    # Only show relevant timing information based on mode
    if not args.upload_only:
        print(f"Extraction Time: {format_time(extraction_time)}")
        print(f"Processing Time: {format_time(processing_time)}")
        if not args.skip_upload:
            print(f"Upload Time: {format_time(upload_time)}")
        print(f"Cleanup Time: {format_time(cleanup_time)}")
    else:
        # In upload-only mode, just show upload time
        print(f"Upload Time: {format_time(upload_time)}")
        
    print("-" * 60)
    print(f"Total Time: {format_time(total_time)}")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
