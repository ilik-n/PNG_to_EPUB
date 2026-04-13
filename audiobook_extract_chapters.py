"""
audiobook_extract_chapters.py
------------------------------
Detects exactly N chapter breaks by finding the top (N-1) longest silences
in an audiobook MP3, embeds chapter markers, and outputs a .m4b file.

Requirements:
    ffmpeg must be on PATH (no pip installs needed)

Usage:
    # Drag the file into the terminal to get its path, then:
    python audiobook_extract_chapters.py "C:\path\to\audiobook.mp3" --output "Danmarks Historie"
    python audiobook_extract_chapters.py "C:\path\to\audiobook.mp3" --output "Danmarks Historie" --chapters 16
    python audiobook_extract_chapters.py "C:\path\to\audiobook.mp3" --output "Danmarks Historie" --chapters 16 --min-silence 0.5 --threshold -40
"""

import argparse
import subprocess
import sys
from pathlib import Path


def detect_silences(input_file: str, min_silence_s: float, threshold_db: int) -> list[dict]:
    """Use ffmpeg silencedetect to find all silence intervals."""
    print(f"Scanning for silences (>= {min_silence_s}s at {threshold_db}dB)...")
    cmd = [
        "ffmpeg", "-i", input_file,
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_s}",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr

    silences = []
    current = {}
    for line in output.splitlines():
        if "silence_start" in line:
            current = {"start": float(line.split("silence_start:")[1].strip())}
        elif "silence_end" in line and current:
            parts = line.split("silence_end:")[1].split("|")
            current["end"] = float(parts[0].strip())
            current["duration"] = float(parts[1].split(":")[1].strip())
            silences.append(current)
            current = {}

    print(f"  Found {len(silences)} silence intervals total.")
    return silences


def pick_chapter_breaks(silences: list[dict], n_chapters: int) -> list[float]:
    """
    Pick exactly (n_chapters - 1) breaks by selecting the longest silences.
    Returns a sorted list of timestamps (in seconds) for chapter boundaries.
    """
    n_breaks = n_chapters - 1
    if len(silences) < n_breaks:
        print(f"\nWARNING: Only {len(silences)} silences found, but need {n_breaks} breaks.")
        print("Try lowering --min-silence or --threshold.\n")
        n_breaks = len(silences)

    # Sort by duration descending, pick top N, then re-sort chronologically
    top = sorted(silences, key=lambda s: s["duration"], reverse=True)[:n_breaks]
    top = sorted(top, key=lambda s: s["start"])

    # Use midpoint of each silence as the chapter boundary
    breaks = [(s["start"] + s["end"]) / 2 for s in top]

    print(f"\n  Top {n_breaks} silences selected as chapter breaks:")
    for i, s in enumerate(top, 1):
        mins = int(s["start"] // 60)
        secs = s["start"] % 60
        print(f"    Break {i:2d}:  {mins:3d}:{secs:05.2f}  (silence: {s['duration']:.2f}s)")

    return breaks


def build_chapters(breaks: list[float], total_duration_s: float) -> list[dict]:
    """Turn a list of break timestamps into chapter dicts."""
    starts = [0.0] + breaks
    chapters = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else total_duration_s
        chapters.append({
            "title": "Indledning" if i == 0 else f"Kapitel {i}",
            "start_ms": int(start * 1000),
            "end_ms": int(end * 1000),
        })
    return chapters


def get_duration(input_file: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def write_ffmetadata(chapters: list[dict], meta_path: str):
    """Write ffmpeg metadata file with chapter markers."""
    lines = [";FFMETADATA1\n\n"]
    for ch in chapters:
        lines.append("[CHAPTER]\n")
        lines.append("TIMEBASE=1/1000\n")
        lines.append(f"START={ch['start_ms']}\n")
        lines.append(f"END={ch['end_ms']}\n")
        lines.append(f"title={ch['title']}\n")
        lines.append("\n")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def build_m4b(input_file: str, meta_path: str, output_file: str):
    """Embed chapters and convert to .m4b."""
    print(f"\nBuilding {output_file} ...")
    cmd = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-i", meta_path,
        "-map_metadata", "1",
        "-map", "0:a",
        "-c:a", "aac",
        "-b:a", "48k",
        output_file
    ]
    subprocess.run(cmd, check=True)


def print_chapter_summary(chapters: list[dict]):
    print(f"\n  Final chapter list:")
    for ch in chapters:
        mins = ch['start_ms'] // 60000
        secs = (ch['start_ms'] % 60000) // 1000
        print(f"    {ch['title']:15s}  {mins:3d}:{secs:02d}")


def main():
    print("\n=== Audiobook Chapterizer ===\n")

    # Prompt for input file (user can drag file into terminal)
    raw = input("Drag the MP3 file here (or paste path): ").strip().strip('"').strip("'")
    input_path = Path(raw)
    if not input_path.exists():
        print(f"Error: file not found: {input_path}")
        sys.exit(1)

    # Prompt for output name
    output_stem = input(f"Output file name (without extension) [{input_path.stem}]: ").strip()
    if not output_stem:
        output_stem = input_path.stem

    meta_path = input_path.parent / (output_stem + ".ffmeta.txt")
    output_path = input_path.parent / (output_stem + ".m4b")

    # Prompt for number of chapters
    chapters_input = input("Number of chapters [16]: ").strip()
    n_chapters = int(chapters_input) if chapters_input else 16

    # Optional: keep --min-silence/--threshold as args for power users
    import argparse
    parser = argparse.ArgumentParser(description="Chapterize an audiobook MP3 into a .m4b", add_help=False)
    parser.add_argument("--min-silence", type=float, default=0.5)
    parser.add_argument("--threshold", type=int, default=-40)
    args, _ = parser.parse_known_args()
    args.chapters = n_chapters

    print(f"\n=== Audiobook Chapterizer ===")
    print(f"Input:     {input_path}")
    print(f"Output:    {output_path}")
    print(f"Chapters:  {args.chapters}")
    print(f"Silence:   >= {args.min_silence}s at {args.threshold}dB\n")

    total_duration = get_duration(str(input_path))
    h = int(total_duration // 3600)
    m = int((total_duration % 3600) // 60)
    print(f"Duration:  {h}h {m}m\n")

    silences = detect_silences(str(input_path), args.min_silence, args.threshold)
    breaks = pick_chapter_breaks(silences, args.chapters)
    chapters = build_chapters(breaks, total_duration)

    print_chapter_summary(chapters)
    write_ffmetadata(chapters, str(meta_path))
    build_m4b(str(input_path), str(meta_path), str(output_path))

    print(f"\n✓ Done! Copy {output_path.name} to your phone and open with Smart AudioBook Player.")


if __name__ == "__main__":
    main()
