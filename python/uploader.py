#!/usr/bin/env python3
# ==============================================================================
# uploader.py – Version 1.1.0 (flat chunk support)
# ==============================================================================
import os, sys, re

def reassemble(chunks_root: str, target_dir: str) -> int:
    """Scan chunks_root for sub‑directories, each with .part* files."""
    if not os.path.isdir(chunks_root): return 0
    os.makedirs(target_dir, exist_ok=True)
    reassembled = 0
    for item in sorted(os.listdir(chunks_root)):
        folder = os.path.join(chunks_root, item)
        if not os.path.isdir(folder): continue
        parts = sorted(f for f in os.listdir(folder) if ".part" in f)
        if not parts: continue
        out_path = os.path.join(target_dir, item)
        with open(out_path, "wb") as out:
            for part_name in parts:
                part_path = os.path.join(folder, part_name)
                with open(part_path, "rb") as pf:
                    out.write(pf.read())
        reassembled += 1
    return reassembled


def reassemble_flat(chunks_dir: str, target_dir: str) -> int:
    """Flat .part files – group by base name (everything before .partNNNN)."""
    if not os.path.isdir(chunks_dir): return 0
    os.makedirs(target_dir, exist_ok=True)
    groups = {}
    for f in sorted(os.listdir(chunks_dir)):
        if ".part" not in f: continue
        base = re.sub(r'\.part\d+$', '', f)
        groups.setdefault(base, []).append(f)
    reassembled = 0
    for base, parts in groups.items():
        parts.sort()
        out_path = os.path.join(target_dir, base)
        with open(out_path, "wb") as out:
            for part_name in parts:
                part_path = os.path.join(chunks_dir, part_name)
                with open(part_path, "rb") as pf:
                    out.write(pf.read())
        reassembled += 1
    return reassembled


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Reassemble chunked files.")
    parser.add_argument("--chunks-dir", default="downloaded_chunks")
    parser.add_argument("--output-dir", default="/home/runner/downloads")
    parser.add_argument("--flat", action="store_true")
    args = parser.parse_args()
    count = reassemble_flat(args.chunks_dir, args.output_dir) if args.flat else reassemble(args.chunks_dir, args.output_dir)
    print(f"Done – reassembled {count} file(s).")
