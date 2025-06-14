#!/usr/bin/env python3
"""
Cache validation utility to check the current cache state and provide recommendations.
"""

import json
import os
import sys

def check_cache_status():
    """Check the current cache status and provide recommendations."""
    
    print("=== CSSFDLP Cache Status Check ===\n")
    
    # Check directories
    processed_dir = "./processed_cstrike"
    old_cache_dir = "./cache/processed_files"
    
    print("Directory Status:")
    print(f"  Processed files directory: {processed_dir}")
    if os.path.exists(processed_dir):
        file_count = sum(len(files) for _, _, files in os.walk(processed_dir))
        print(f"    ✓ EXISTS ({file_count} files)")
    else:
        print(f"    ✗ MISSING")
    
    print(f"  Old cache directory: {old_cache_dir}")
    if os.path.exists(old_cache_dir):
        cache_file_count = sum(len(files) for _, _, files in os.walk(old_cache_dir))
        print(f"    ⚠  EXISTS ({cache_file_count} files) - should be empty")
    else:
        print(f"    ✓ EMPTY/MISSING (good)")
    
    # Check cache files
    print("\nCache Files Status:")
    cache_files = [
        (".upload_state.json", "Upload state cache"),
        (".remote_md5s.json", "Remote MD5 cache"),
        (".remote_timestamps.json", "Remote timestamp cache")
    ]
    
    cache_status = {}
    for cache_file, description in cache_files:
        file_path = os.path.join(processed_dir, cache_file)
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)
                entries = len(data) if isinstance(data, dict) else 0
                print(f"  ✓ {description}: {entries} entries")
                cache_status[cache_file] = entries
            except Exception as e:
                print(f"  ✗ {description}: ERROR reading file - {e}")
                cache_status[cache_file] = "error"
        else:
            print(f"  ✗ {description}: MISSING")
            cache_status[cache_file] = 0
    
    # Analysis and recommendations
    print("\n=== Analysis ===")
    
    upload_state_entries = cache_status.get(".upload_state.json", 0)
    
    if upload_state_entries > 0:
        print(f"✓ Upload state cache has {upload_state_entries} entries")
        print("  → Next run should skip uploading unchanged files")
    else:
        print("⚠ Upload state cache is missing or empty")
        print("  → Next run will upload ALL files (may take longer)")
    
    # Count actual processed files
    processed_files = 0
    if os.path.exists(processed_dir):
        for root, dirs, files in os.walk(processed_dir):
            for file in files:
                if not file.startswith('.'):  # Skip cache files
                    processed_files += 1
    
    print(f"\nProcessed files: {processed_files}")
    
    if upload_state_entries > 0 and processed_files > 0:
        coverage = (upload_state_entries / processed_files) * 100
        print(f"Cache coverage: {coverage:.1f}%")
        
        if coverage >= 90:
            print("✓ Good cache coverage - uploads should be minimal")
        elif coverage >= 50:
            print("⚠ Partial cache coverage - some files may be re-uploaded")
        else:
            print("✗ Poor cache coverage - many files may be re-uploaded")
    
    print("\n=== Recommendations ===")
    
    if upload_state_entries == 0 and processed_files > 0:
        print("• Run 'python migrate_cache_fix.py' to create upload state cache")
    
    if os.path.exists(old_cache_dir):
        print("• Old cache directory still exists - consider removing it after migration")
    
    if cache_status.get(".upload_state.json", 0) > 0:
        print("• Cache looks good - next upload should only process changed files")
    else:
        print("• No upload cache - next run will upload all files")
    
    print("\nFor best performance, ensure upload state cache exists before running uploads.")

if __name__ == "__main__":
    check_cache_status()
