# Counter-Strike Source FastDL Processor

This script automates the process of preparing Counter-Strike Source files for a FastDL server:
1. Unzips a downloaded `/cstrike` folder
2. Compresses maps and audio files with bzip2 (which need to be compressed for FastDL)
3. Uploads all files to an S3 bucket, maintaining the original directory structure

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
s3://your-bucket/cstrike/sound/ambient/whatever.wav.bz2
s3://your-bucket/cstrike/materials/models/props/whatever.vmt
```


```bash
python cssfdlp.py cstrike.zip --bucket your-vultr-bucket --endpoint-url https://ewr1.vultrobjects.com --aws-profile vultr
```

## Logging

The script logs all operations to both the console and a file named `cssfdlp.log` in the same directory as the script.
