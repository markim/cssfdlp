# Counter-Strike Source FastDL Processor

This script automates the process of preparing Counter-Strike Source files for a FastDL server:
1. Unzips a downloaded `/cstrike` folder
2. Compresses maps and audio files with bzip2 (which need to be compressed for FastDL)
3. Creates MD5 hash files for all processed files for verification
4. Uploads all files to an S3 bucket, maintaining the original directory structure
5. Uses MD5 verification to skip uploading unchanged files, making subsequent uploads much faster

## Prerequisites

- Python 3.6 or newer
- AWS account with S3 bucket OR Vultr Object Storage account
- AWS CLI configured with credentials (used for both AWS and Vultr authentication)
- bzip2 command-line tool installed on your system

### Installing Dependencies

```bash
./install.sh
```

On Windows, make sure bzip2 is installed and available in your PATH. You can install it using:
```bash
# Using Chocolatey
choco install bzip2

# Using winget
winget install bzip2
```

## Usage

Basic usage:
```bash
python cssfdlp.py cstrike.zip --bucket your-s3-bucket-name
```

Advanced options:
```bash
python cssfdlp.py cstrike.zip --bucket your-s3-bucket-name --aws-profile myprofile --output-dir ./processed --keep-temp
```

### Environment Variables

Instead of passing command-line arguments every time, you can use environment variables:

1. Copy the example environment file and edit it with your settings:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file with your configuration:
   ```
   AWS_BUCKET_NAME=your-s3-bucket-name
   AWS_ENDPOINT_URL=https://ewr1.vultrobjects.com
   AWS_ACCESS_KEY_ID=your-access-key-id
   AWS_SECRET_ACCESS_KEY=your-secret-access-key
   OUTPUT_DIR=./processed_cstrike
   SKIP_UPLOAD=False
   KEEP_TEMP=False
   ```

3. Run the script with minimal arguments:
   ```bash
   python cssfdlp.py cstrike.zip
   ```

Note: Command-line arguments will override environment variables when both are specified.

### Command-line Arguments

- `zip_path`: Path to the downloaded cstrike zip file
- `--bucket`: (Required) S3 bucket name to upload the processed files
- `--aws-profile`: AWS profile name to use (optional, uses default if not specified)
- `--endpoint-url`: S3-compatible endpoint URL (required for Vultr and other S3-compatible storage)
- `--output-dir`: Directory to store processed files (default: ./processed_cstrike)
- `--skip-upload`: Skip uploading to S3 (useful for testing)
- `--keep-temp`: Keep temporary extraction files after processing

## File Compression

The following file types are automatically compressed with bzip2:
- `.bsp` (maps)
- `.nav` (navigation meshes)
- `.ain` (AI nodes)
- `.wav`, `.mp3`, `.ogg` (audio files)

## S3 Structure

Files are uploaded to the S3 bucket with the same structure as the original cstrike folder:
```
s3://your-bucket/cstrike/maps/de_dust2.bsp.bz2
s3://your-bucket/cstrike/maps/de_dust2.bsp.bz2.md5
s3://your-bucket/cstrike/sound/ambient/whatever.wav.bz2
s3://your-bucket/cstrike/sound/ambient/whatever.wav.bz2.md5
s3://your-bucket/cstrike/materials/models/props/whatever.vmt
s3://your-bucket/cstrike/materials/models/props/whatever.vmt.md5
```

## MD5 Verification

The script now creates MD5 hash files for all processed files during backup creation. These MD5 files are used during upload to verify file integrity and skip uploading files that haven't changed, making subsequent uploads much faster.

### How it works:
1. **During Processing**: For each processed file, an MD5 hash is calculated and saved to a `.md5` file
2. **During Upload**: Before uploading a file, the script compares the local MD5 hash with the remote MD5 file on S3
3. **Smart Skipping**: Files with matching MD5 hashes are skipped, significantly reducing upload time for unchanged files
4. **Integrity**: This ensures that only files that have actually changed are uploaded, while maintaining data integrity

The MD5 files use the standard format: `<hash> *<filename>`


```bash
python cssfdlp.py cstrike.zip --bucket your-vultr-bucket --endpoint-url https://ewr1.vultrobjects.com --aws-profile vultr
```

## Logging

The script logs all operations to both the console and a file named `cssfdlp.log` in the same directory as the script.


# CSSFDLP Modular Structure

This Section describes the modular breakdown of the Counter-Strike Source FastDL Processor.

## Overview

The original monolithic `cssfdlp.py` script (2,846 lines) has been broken down into logical modules for better maintainability, testing, and code organization.

## Module Structure

```
cssfdlp/
├── cssfdlp.py                  # Main entry point (modular version)
├── cssfdlp_original.py         # Original monolithic script (backup)
├── src/                        # Source modules
│   ├── __init__.py            # Package initialization
│   ├── config.py              # Configuration constants and settings
│   ├── logger.py              # Logging utilities and colored output
│   ├── file_utils.py          # File operations, MD5 calculation
│   ├── cache_manager.py       # Cache management for processed files
│   ├── compression.py         # File compression using bzip2
│   ├── remote_handler.py      # SSH connections, zip creation/downloads
│   ├── s3_uploader.py         # S3 upload with parallel processing
│   ├── processor.py           # Main file processing logic
│   └── cli.py                 # Command-line argument parsing
└── requirements.txt           # Python dependencies
```

## Module Descriptions

### `src/config.py`
- Contains all configuration constants and default values
- File extensions for compression
- Cache directory paths
- Performance settings

### `src/logger.py`
- Colored console output with ANSI support
- File and console logging setup
- Progress tracking utilities
- Performance summary logging

### `src/file_utils.py`
- MD5 calculation and verification
- File comparison utilities
- Auto-exclude pattern handling
- Rsync-like file copying

### `src/cache_manager.py`
- Cache directory management
- Legacy cache migration
- Remote MD5 storage and retrieval
- Deterministic cache paths

### `src/compression.py`
- Bzip2 compression with MD5 preservation
- Compression requirement checking
- Cache-aware compression decisions

### `src/remote_handler.py`
- SSH connection management
- Remote zip creation with optimization
- File downloads from remote servers
- Zip extraction with progress tracking

### `src/s3_uploader.py`
- Parallel S3 uploads
- MD5-based change detection
- Upload progress tracking
- Error handling and retry logic

### `src/processor.py`
- Main file processing workflow
- Intelligent caching decisions
- Progress tracking
- Statistics collection

### `src/cli.py`
- Command-line argument parsing
- Environment variable support
- Input validation

## Benefits of Modular Structure

1. **Maintainability**: Each module has a single responsibility
2. **Testability**: Individual modules can be tested in isolation
3. **Reusability**: Modules can be imported and used independently
4. **Readability**: Smaller, focused files are easier to understand
5. **Collaboration**: Multiple developers can work on different modules
6. **Debugging**: Issues can be isolated to specific modules

## Usage

The modular version maintains the same command-line interface as the original:

```bash
python cssfdlp.py [zip_path] [options]
```

All existing functionality is preserved, including:
- Remote zip creation and downloading
- File compression and caching
- S3 upload with MD5 verification
- Progress tracking and logging
- Environment variable configuration

## Migration Notes

- The original script is preserved as `cssfdlp_original.py`
- All functionality is maintained in the modular version
- Configuration and behavior remain identical
- Performance characteristics are preserved

## Development

To add new features or modify existing ones:

1. Identify the appropriate module based on functionality
2. Make changes within the relevant module
3. Update imports in `__init__.py` if needed
4. Test the specific module and overall integration

## Dependencies

All modules share the same dependencies as the original script:
- boto3 (AWS SDK)
- paramiko (SSH client)
- requests (HTTP client)
- python-dotenv (environment variables)
- Standard library modules (os, sys, time, etc.)
