"""
Logging utilities and colored console output for cssfdlp.
"""

import logging
import os
import sys


class ColoredFormatter(logging.Formatter):
    """Custom formatter with color support for different log levels"""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[41m",  # Red background
        "RESET": "\033[0m",  # Reset to default
    }

    def format(self, record):
        # Force colored output for Windows by enabling ANSI
        if os.name == "nt":
            os.system("color")  # Enable ANSI support on Windows

        # Determine prefix based on message content or level
        message = record.getMessage()
        prefix = None
        color = self.COLORS.get(record.levelname, self.COLORS["INFO"])
        reset = self.COLORS["RESET"]

        # Check for specific prefixes in the message
        if (
            message.startswith("STEP ")
            or "COUNTER-STRIKE SOURCE" in message
            or "PROCESSING COMPLETED" in message
        ):
            prefix = "EXECUTE"
            color = "\033[35m"  # Purple for execution steps
        elif message.startswith("Progress:") or "Processing" in message:
            prefix = "PROGRESS"
            color = "\033[34m"  # Blue for progress
        elif (
            message.startswith("CONFIGURATION:")
            or message.startswith("Bucket:")
            or message.startswith("Endpoint URL:")
            or message.startswith("Output Directory:")
            or message.startswith("Skip Upload:")
            or message.startswith("Keep Temporary Files:")
            or message.startswith("Upload Only Mode:")
            or message.startswith("Processing folders:")
            or message.startswith("AWS Credentials:")
        ):
            prefix = "CONFIG"
            color = "\033[36m"  # Cyan for configuration
        elif "error" in message.lower() or record.levelname == "ERROR":
            prefix = "ERROR"
            color = "\033[31m"  # Red for errors
        elif "warning" in message.lower() or record.levelname == "WARNING":
            prefix = "WARNING"
            color = "\033[33m"  # Yellow for warnings
        elif (
            "found" in message.lower()
            or "installed" in message.lower()
            or "completed" in message.lower()
            or "success" in message.lower()
        ):
            prefix = "SUCCESS"
            color = "\033[32m"  # Green for success
        else:
            prefix = "INFO"
            color = "\033[37m"  # White for general info

        # Format with colored prefix
        formatted_message = f"{color}[{prefix}]{reset} {message}"

        # Flush output immediately
        sys.stdout.flush()

        return formatted_message


def setup_logger():
    """Set up and configure the logger with file and console handlers."""
    # First, make sure the log directory exists
    os.makedirs(os.path.dirname(os.path.abspath("cssfdlp.log")), exist_ok=True)

    # Initialize the logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.handlers = []  # Clear any existing handlers to avoid duplicates

    # Add file handler with timestamps and level
    file_handler = logging.FileHandler("cssfdlp.log", mode="a", encoding="utf-8")
    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Add console handler with simplified format and immediate flushing
    console_handler = logging.StreamHandler(sys.stdout)
    console_formatter = ColoredFormatter()
    console_handler.setFormatter(console_formatter)
    # Force immediate flushing for real-time output
    (
        console_handler.stream.reconfigure(line_buffering=True)
        if hasattr(console_handler.stream, "reconfigure")
        else None
    )
    logger.addHandler(console_handler)

    # Configure Python to flush output immediately
    sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None

    return logger


# Initialize logger
logger = setup_logger()


def log_step(message):
    """Log a major processing step with visual separation"""
    logger.info(f"\n{'='*70}\n{message}\n{'='*70}")
    sys.stdout.flush()  # Ensure immediate output


def log_progress(percentage, count=None, total=None, extra_info=None):
    """Log a progress update in a standardized format"""
    if count is not None and total is not None:
        progress_msg = f"Progress: {percentage:.1f}% ({count}/{total})"
    else:
        progress_msg = f"Progress: {percentage:.1f}%"

    if extra_info:
        progress_msg += f" - {extra_info}"

    logger.info(progress_msg)
    sys.stdout.flush()  # Ensure immediate output


def log_info(message, prefix="INFO"):
    """Log an info message with custom prefix"""
    logger.info(message)
    sys.stdout.flush()  # Ensure immediate output


def log_error(message, prefix="ERROR"):
    """Log an error message with proper formatting"""
    logger.error(message)
    sys.stdout.flush()  # Ensure immediate output


def log_success(message, prefix="SUCCESS"):
    """Log a success message with proper formatting"""
    logger.info(message)
    sys.stdout.flush()  # Ensure immediate output


def log_progress_grouped(
    percentage, count=None, total=None, extra_info=None, last_logged_percentage=None
):
    """Log a progress update only at 10% increments to reduce verbosity"""
    # Round to nearest 10% for grouping
    group_percentage = round(percentage / 10) * 10

    # Only log if we've reached a new 10% milestone or it's the first/last item
    if (
        last_logged_percentage is None
        or group_percentage > last_logged_percentage
        or percentage == 100.0
        or (count is not None and count == 1)
    ):

        if count is not None and total is not None:
            progress_msg = f"Progress: {group_percentage:.0f}% ({count}/{total})"
        else:
            progress_msg = f"Progress: {group_percentage:.0f}%"

        if extra_info:
            progress_msg += f" - {extra_info}"

        logger.info(progress_msg)
        return group_percentage

    return last_logged_percentage


def format_time(seconds):
    """Format time duration in a human-readable format."""
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


def log_performance_summary(
    start_time,
    extraction_time,
    processing_time,
    upload_time,
    remote_zip_time=0,
    cached_count=0,
    compressed_count=0,
    skipped_count=0,
    upload_count=0,
):
    """Log a comprehensive performance summary."""
    # Performance tracking is handled by the caller
    total_processed = cached_count + compressed_count + skipped_count

    logger.info("\nPERFORMANCE SUMMARY:")
    logger.info(f"  Files Processed: {total_processed}")
    logger.info(f"  - Cached (reused): {cached_count}")
    logger.info(f"  - Compressed: {compressed_count}")
    logger.info(f"  - Skipped (up-to-date): {skipped_count}")

    if upload_count > 0:
        logger.info(f"  Files Uploaded: {upload_count}")

    if total_processed > 0:
        cache_efficiency = (cached_count / total_processed) * 100
        skip_efficiency = (skipped_count / total_processed) * 100
        logger.info("\nEFFICIENCY METRICS:")
        logger.info(f"  Cache Hit Rate:    {cache_efficiency:.1f}%")
        logger.info(f"  Skip Rate (MD5):   {skip_efficiency:.1f}%")
        logger.info(f"  Overall Efficiency: {(cache_efficiency + skip_efficiency):.1f}%")

    logger.info("=" * 70)
