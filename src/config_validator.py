"""
Configuration validation and management for cssfdlp.
Provides type-safe configuration handling with comprehensive validation.
"""

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .logger import logger


@dataclass
class SSHConfig:
    """SSH connection configuration."""

    host: str
    user: str
    password: Optional[str] = None
    key_file: Optional[str] = None
    port: int = 22
    path: str = "/path/to/cstrike"

    def __post_init__(self):
        if not self.password and not self.key_file:
            raise ValueError("Either password or key_file must be provided for SSH")
        if self.key_file:
            self.key_file = os.path.expanduser(self.key_file)
            if not os.path.exists(self.key_file):
                raise FileNotFoundError(f"SSH key file not found: {self.key_file}")
        if not (1 <= self.port <= 65535):
            raise ValueError(f"Invalid SSH port: {self.port}")
        if not self.host:
            raise ValueError("SSH host cannot be empty")
        if not self.user:
            raise ValueError("SSH user cannot be empty")


@dataclass
class S3Config:
    """S3/Object Storage configuration."""

    bucket_name: str
    endpoint_url: Optional[str] = None
    region: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    upload_workers: int = 10

    def __post_init__(self):
        if not self.bucket_name:
            raise ValueError("S3 bucket name cannot be empty")
        if not (1 <= self.upload_workers <= 50):
            raise ValueError(f"Upload workers must be between 1-50, got: {self.upload_workers}")
        # Validate bucket name format
        if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", self.bucket_name.lower()):
            logger.warning(f"S3 bucket name may not be valid: {self.bucket_name}")


@dataclass
class ProcessingConfig:
    """File processing configuration."""

    output_dir: str = "./processed_cstrike"
    skip_upload: bool = False
    keep_temp: bool = False
    upload_only: bool = False
    compression_level: int = 9
    parallel_workers: int = 4

    def __post_init__(self):
        self.output_dir = os.path.abspath(self.output_dir)
        if not (1 <= self.compression_level <= 9):
            raise ValueError(f"Compression level must be 1-9, got: {self.compression_level}")
        if not (1 <= self.parallel_workers <= 16):
            raise ValueError(f"Parallel workers must be 1-16, got: {self.parallel_workers}")


@dataclass
class AppConfig:
    """Main application configuration."""

    s3: S3Config
    processing: ProcessingConfig
    ssh: Optional[SSHConfig] = None
    remote_zip_url: Optional[str] = None
    create_remote_zip: bool = False
    zip_path: Optional[str] = None

    def __post_init__(self):
        # Validate operation mode
        modes = sum(
            [
                bool(self.zip_path),
                bool(self.create_remote_zip),
                bool(self.remote_zip_url),
                self.processing.upload_only,
            ]
        )

        if modes > 1:
            raise ValueError(
                "Only one operation mode can be specified: zip_path, create_remote_zip, remote_zip_url, or upload_only"
            )

        if modes == 0:
            raise ValueError("At least one operation mode must be specified")

        if self.create_remote_zip and not self.ssh:
            raise ValueError("SSH configuration required when create_remote_zip is True")

        if self.remote_zip_url and not self.remote_zip_url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid remote zip URL: {self.remote_zip_url}")

        if self.zip_path and not os.path.exists(self.zip_path):
            raise FileNotFoundError(f"Zip file not found: {self.zip_path}")

        if self.processing.upload_only and not os.path.exists(self.processing.output_dir):
            raise FileNotFoundError(
                f"Output directory not found for upload-only mode: {self.processing.output_dir}"
            )


class ConfigValidator:
    """Configuration validator and loader."""

    @staticmethod
    def _get_env_bool(key: str, default: bool = False) -> bool:
        """Get boolean value from environment variable."""
        value = os.environ.get(key, "").lower()
        return value in ("true", "yes", "1", "on")

    @staticmethod
    def _get_env_int(key: str, default: int, min_val: int = None, max_val: int = None) -> int:
        """Get integer value from environment variable with validation."""
        try:
            value = int(os.environ.get(key, str(default)))
            if min_val is not None and value < min_val:
                raise ValueError(f"{key} must be >= {min_val}, got: {value}")
            if max_val is not None and value > max_val:
                raise ValueError(f"{key} must be <= {max_val}, got: {value}")
            return value
        except ValueError as e:
            if "invalid literal" in str(e):
                raise ValueError(f"Invalid integer value for {key}: {os.environ.get(key)}")
            raise

    @classmethod
    def from_args_and_env(cls, args) -> AppConfig:
        """Create configuration from command line arguments and environment variables."""

        # S3 Configuration
        s3_config = S3Config(
            bucket_name=args.bucket,
            endpoint_url=args.endpoint_url,
            region=os.environ.get("AWS_REGION_NAME"),
            access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            upload_workers=cls._get_env_int("AWS_UPLOAD_WORKERS", 10, 1, 50),
        )

        # Processing Configuration
        processing_config = ProcessingConfig(
            output_dir=args.output_dir,
            skip_upload=args.skip_upload,
            keep_temp=args.keep_temp,
            upload_only=args.upload_only,
            compression_level=cls._get_env_int("COMPRESSION_LEVEL", 9, 1, 9),
            parallel_workers=cls._get_env_int("PARALLEL_WORKERS", 4, 1, 16),
        )

        # SSH Configuration (if needed)
        ssh_config = None
        if args.create_remote_zip:
            ssh_config = SSHConfig(
                host=args.remote_host,
                user=args.remote_user,
                password=args.remote_password,
                key_file=args.remote_key_file,
                port=args.remote_port,
                path=args.remote_path,
            )

        # Main Configuration
        config = AppConfig(
            s3=s3_config,
            processing=processing_config,
            ssh=ssh_config,
            remote_zip_url=args.remote_zip_url,
            create_remote_zip=args.create_remote_zip,
            zip_path=args.zip_path,
        )

        return config

    @staticmethod
    def validate_runtime_requirements():
        """Validate runtime requirements and dependencies."""
        errors = []

        # Check for required external tools
        import shutil

        if not shutil.which("bzip2"):
            errors.append("bzip2 command not found. Please install bzip2.")

        if not shutil.which("rsync"):
            logger.warning(
                "rsync not found. Incremental sync features will be disabled."
            )  # Check Python dependencies
        try:
            import boto3  # noqa: F401
            import paramiko  # noqa: F401
            import requests  # noqa: F401
        except ImportError as e:
            errors.append(f"Missing Python dependency: {e}")

        if errors:
            raise RuntimeError(
                "Runtime validation failed:\n" + "\n".join(f"- {error}" for error in errors)
            )

        logger.info("Runtime requirements validated successfully")


# Performance monitoring utilities
class PerformanceMetrics:
    """Performance metrics collection and reporting."""

    def __init__(self):
        self.metrics: Dict[str, Dict[str, Any]] = {}
        self.start_times: Dict[str, float] = {}

    def start_operation(self, operation: str):
        """Start timing an operation."""
        import time

        self.start_times[operation] = time.time()

    def end_operation(self, operation: str, **metadata):
        """End timing an operation and record metrics."""
        import time

        if operation not in self.start_times:
            logger.warning(f"No start time recorded for operation: {operation}")
            return

        duration = time.time() - self.start_times[operation]
        self.metrics[operation] = {"duration": duration, "timestamp": time.time(), **metadata}
        del self.start_times[operation]

    def get_summary(self) -> Dict[str, Any]:
        """Get performance summary."""
        total_time = sum(m["duration"] for m in self.metrics.values())
        return {
            "total_duration": total_time,
            "operations": len(self.metrics),
            "breakdown": {op: m["duration"] for op, m in self.metrics.items()},
            "detailed_metrics": self.metrics,
        }

    def log_summary(self):
        """Log performance summary."""
        summary = self.get_summary()
        logger.info("=== PERFORMANCE SUMMARY ===")
        logger.info(f"Total Duration: {summary['total_duration']:.2f}s")
        logger.info(f"Operations: {summary['operations']}")

        for operation, duration in summary["breakdown"].items():
            percentage = (
                (duration / summary["total_duration"]) * 100 if summary["total_duration"] > 0 else 0
            )
            logger.info(f"  {operation}: {duration:.2f}s ({percentage:.1f}%)")


# Global performance metrics instance
performance_metrics = PerformanceMetrics()
