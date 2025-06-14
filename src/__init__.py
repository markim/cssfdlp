"""
Counter-Strike Source FastDL Processor (cssfdlp)

A modular toolkit for processing CS:S files for FastDL servers.
"""

__version__ = "1.0.0"
__author__ = "cssfdlp"
__description__ = "Counter-Strike Source FastDL Processor"

# Import main components
from .cache_manager import ensure_cache_dirs, get_cached_processed_path_with_fallback
from .compression import compress_file, file_needs_compression, should_compress_file
from .config import ALLOWED_FASTDL_FOLDERS, COMPRESS_EXTENSIONS, VERSION
from .file_utils import copy_with_rsync_logic, read_auto_exclude, should_exclude
from .logger import log_error, log_info, log_performance_summary, log_step, log_success, logger
from .processor import process_files
from .remote_handler import (
    create_remote_zip,
    download_remote_zip,
    download_zip_from_url,
    extract_zip,
)
from .s3_uploader import upload_to_s3
