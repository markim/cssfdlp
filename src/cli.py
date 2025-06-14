"""
Command-line argument parsing for cssfdlp.
"""

import argparse
import os


def _get_env_bool(key: str) -> bool:
    """Get boolean value from environment variable."""
    return os.environ.get(key, "").lower() in ("true", "yes", "1")


def _get_env_int(key: str, default: int) -> int:
    """Get integer value from environment variable."""
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def parse_arguments():
    """Parse command-line arguments with enhanced validation."""
    parser = argparse.ArgumentParser(
        description="Process CS:S files for FastDL server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  AWS_BUCKET_NAME         S3 bucket name
  AWS_ENDPOINT_URL        S3 endpoint URL
  AWS_UPLOAD_WORKERS      Number of parallel upload workers (1-50, default: 10)
  OUTPUT_DIR              Directory to store processed files
  SKIP_UPLOAD             Skip uploading to S3 (true/false)
  KEEP_TEMP               Keep temporary files (true/false)
  UPLOAD_ONLY             Upload-only mode (true/false)
  COMPRESSION_LEVEL       Bzip2 compression level (1-9, default: 9)
  PARALLEL_WORKERS        Number of parallel processing workers (1-16, default: 4)
  REMOTE_HOST             Remote server hostname/IP
  REMOTE_USER             Remote server username
  REMOTE_PASSWORD         Remote server password
  REMOTE_KEY_FILE         Path to SSH private key file
  REMOTE_PORT             Remote server SSH port (default: 22)
  REMOTE_PATH             Remote path to cstrike directory
  REMOTE_ZIP_URL          URL to download zip file  CREATE_REMOTE_ZIP       Create zip on remote server (true/false)

Examples:
  # Local zip file
  python cssfdlp.py cstrike.zip --bucket my-bucket

  # SSH remote zip creation
  python cssfdlp.py --create-remote-zip --bucket my-bucket

  # URL download
  python cssfdlp.py --remote-zip-url https://example.com/cstrike.zip --bucket my-bucket

  # Upload only mode
  python cssfdlp.py --upload-only --bucket my-bucket
        """,
    )

    # Positional arguments
    parser.add_argument("zip_path", help="Path to the downloaded cstrike zip file", nargs="?")

    # S3 Configuration
    s3_group = parser.add_argument_group("S3 Configuration")
    s3_group.add_argument(
        "--bucket",
        default=os.environ.get("AWS_BUCKET_NAME"),
        help="S3 bucket name (env: AWS_BUCKET_NAME)",
    )
    s3_group.add_argument(
        "--endpoint-url",
        default=os.environ.get("AWS_ENDPOINT_URL"),
        help="S3 endpoint URL (env: AWS_ENDPOINT_URL)",
    )

    # Processing Configuration
    proc_group = parser.add_argument_group("Processing Configuration")
    proc_group.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", "./processed_cstrike"),
        help="Directory to store processed files (env: OUTPUT_DIR)",
    )
    proc_group.add_argument(
        "--skip-upload",
        action="store_true",
        default=_get_env_bool("SKIP_UPLOAD"),
        help="Skip uploading to S3 (env: SKIP_UPLOAD)",
    )
    proc_group.add_argument(
        "--keep-temp",
        action="store_true",
        default=_get_env_bool("KEEP_TEMP"),
        help="Keep temporary files after processing (env: KEEP_TEMP)",
    )
    proc_group.add_argument(
        "--upload-only",
        action="store_true",
        default=_get_env_bool("UPLOAD_ONLY"),
        help="Skip extraction and processing, only upload files from output directory (env: UPLOAD_ONLY)",
    )

    # Remote server options
    remote_group = parser.add_argument_group("Remote Server Configuration")
    remote_group.add_argument(
        "--remote-host",
        default=os.environ.get("REMOTE_HOST"),
        help="Remote server hostname/IP (env: REMOTE_HOST)",
    )
    remote_group.add_argument(
        "--remote-user",
        default=os.environ.get("REMOTE_USER"),
        help="Remote server username (env: REMOTE_USER)",
    )
    remote_group.add_argument(
        "--remote-password",
        default=os.environ.get("REMOTE_PASSWORD"),
        help="Remote server password (env: REMOTE_PASSWORD)",
    )
    remote_group.add_argument(
        "--remote-key-file",
        default=os.environ.get("REMOTE_KEY_FILE"),
        help="Path to SSH private key file (env: REMOTE_KEY_FILE)",
    )
    remote_group.add_argument(
        "--remote-port",
        type=int,
        default=_get_env_int("REMOTE_PORT", 22),
        help="Remote server SSH port (env: REMOTE_PORT, default: 22)",
    )
    remote_group.add_argument(
        "--remote-path",
        default=os.environ.get("REMOTE_PATH", "/path/to/cstrike"),
        help="Remote path to cstrike directory (env: REMOTE_PATH)",
    )
    remote_group.add_argument(
        "--remote-zip-url",
        default=os.environ.get("REMOTE_ZIP_URL"),
        help="URL to download the zip from remote server (env: REMOTE_ZIP_URL)",
    )
    remote_group.add_argument(
        "--create-remote-zip",
        action="store_true",
        default=_get_env_bool("CREATE_REMOTE_ZIP"),
        help="Create zip file on remote server before downloading (env: CREATE_REMOTE_ZIP)",
    )

    # Maintenance operations
    maintenance_group = parser.add_argument_group("maintenance operations")
    maintenance_group.add_argument(
        "--validate-md5",
        action="store_true",
        help="Validate and fix all MD5 files in the output directory",
    )

    args = parser.parse_args()
    return args
