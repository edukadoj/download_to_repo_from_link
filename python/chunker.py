#!/usr/bin/env python3
# Version: 3.2 – Generates a batch file that reassembles ALL chunked files
"""
chunker.py - Split files into chunks and generate a reassemble.bat that works for multiple files.

Usage:
    python chunker.py --file <input_file> --output-dir <dir> [--chunk-size MB]
    python chunker.py --files file1 file2 ... --output-dir <dir> [--chunk-size MB]

Outputs:
    - Chunk files: <original_name>.part####
    - reassemble.bat – loops over all .part0000 files and reassembles each original file.
"""

import os
import sys
import argparse
import math
from pathlib import Path

DEFAULT_CHUNK_MB = 20

def split_file(input_path: Path, output_dir: Path, chunk_size: int, base_name: str = None):
    """Split a single file into chunks using the original filename (preserves Unicode)."""
    if base_name is None:
        base_name = input_path.stem
    ext = input_path.suffix
    full_base = f"{base_name}{ext}"
    output_dir.mkdir(parents=True, exist_ok=True)

    file_size = input_path.stat().st_size
    num_chunks = math.ceil(file_size / chunk_size)
    print(f"Splitting {input_path} ({file_size} bytes) into {num_chunks} chunk(s)")

    chunk_paths = []
    with open(input_path, 'rb') as f:
        for i in range(num_chunks):
            chunk_data = f.read(chunk_size)
            chunk_name = f"{full_base}.part{i:04d}"
            chunk_path = output_dir / chunk_name
            with open(chunk_path, 'wb') as cf:
                cf.write(chunk_data)
            chunk_paths.append(chunk_path)
            print(f"  Created {chunk_name} ({len(chunk_data)} bytes)")
    return chunk_paths, full_base

def generate_reassemble_bat(output_dir: Path):
    """
    Generate a batch file that reassembles ALL chunked files.
    It finds every unique base name by scanning .part0000 files and processes each.
    """
    bat_content = '''@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo  Reassembling all files from chunks...
echo ========================================

:: Process each distinct base name (from .part0000 files)
for %%f in ("*.part0000") do (
    set "full=%%f"
    set "base=!full:.part0000=!"

    :: Skip if this base name was already processed
    if not defined _processed_!base! (
        set "_processed_!base!=1"

        echo.
        echo --- Rebuilding "!base!" ---
        if exist "!base!.part*" (
            copy /b "!base!.part*" "!base!" >nul
            if !errorlevel! equ 0 (
                echo Successfully created "!base!"
                del "!base!.part*" 2>nul
                echo Deleted temporary parts.
            ) else (
                echo ERROR: Failed to reassemble "!base!".
            )
        ) else (
            echo WARNING: No parts found for "!base!".
        )
    )
)

echo.
echo ========================================
echo All done! Press any key to exit.
pause >nul
'''
    bat_path = output_dir / "reassemble.bat"
    with open(bat_path, 'w', encoding='utf-8') as f:
        f.write(bat_content)
    print(f"Generated reassembly script: {bat_path}")

def main():
    parser = argparse.ArgumentParser(description="Split files into chunks and generate reassembly batch file (supports multiple files)")
    parser.add_argument('--file', help='Single file to split')
    parser.add_argument('--files', nargs='+', help='Multiple files to split')
    parser.add_argument('--output-dir', required=True, help='Directory to store chunks and reassemble script')
    parser.add_argument('--chunk-size', type=int, default=DEFAULT_CHUNK_MB, help=f'Chunk size in MB (default {DEFAULT_CHUNK_MB})')
    parser.add_argument('--base-name', help='Base name for chunks (only for single file, overrides original name)')
    args = parser.parse_args()

    if not args.file and not args.files:
        print("Error: Either --file or --files must be provided", file=sys.stderr)
        sys.exit(1)

    input_paths = []
    if args.file:
        input_paths.append(Path(args.file))
    else:
        input_paths = [Path(f) for f in args.files]

    output_dir = Path(args.output_dir)
    chunk_size = args.chunk_size * 1024 * 1024

    for input_path in input_paths:
        if not input_path.exists():
            print(f"Error: File not found: {input_path}", file=sys.stderr)
            sys.exit(1)
        base_name = args.base_name if args.file and len(input_paths) == 1 else None
        split_file(input_path, output_dir, chunk_size, base_name)

    generate_reassemble_bat(output_dir)
    print("Done.")

if __name__ == '__main__':
    main()
