import os
import sys
from PIL import Image

KINDLE_FIRE_SIZE = (600, 1024)   # change to (600, 1024) if your images are portrait
GRAY_SHADES      = 16            # number of gray levels (16 = e-ink friendly, very small)

def process_images(input_folder, output_folder):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    png_files = [f for f in os.listdir(input_folder) if f.lower().endswith(".png")]

    if not png_files:
        print("No PNG files found in the specified folder.")
        return

    print(f"\nFound {len(png_files)} PNG file(s). Processing...\n")

    for filename in png_files:
        img_path = os.path.join(input_folder, filename)
        try:
            with Image.open(img_path) as img:
                # 1. Convert to grayscale
                gray = img.convert("L")

                # 2. Resize to fit Kindle Fire screen, preserving aspect ratio
                gray.thumbnail(KINDLE_FIRE_SIZE, Image.LANCZOS)

                # 3. Quantize to 16 gray shades
                step = 256 // GRAY_SHADES
                quantized = gray.point(lambda p: (p // step) * step)

                # 4. Save with max PNG compression (level 9)
                save_path = os.path.join(output_folder, filename)
                quantized.save(save_path, "PNG", optimize=True, compress_level=9)

                original_size = os.path.getsize(img_path) / 1024
                new_size = os.path.getsize(save_path) / 1024
                ratio = (1 - new_size / original_size) * 100
                print(f"  ✓ {filename}  ({original_size:.1f} KB → {new_size:.1f} KB, -{ratio:.0f}%)")
        except Exception as e:
            print(f"  ✗ Error processing {filename}: {e}")

    print(f"\nDone! Output saved to: {output_folder}")

if __name__ == "__main__":
    print("=== PNG to Kindle Fire Optimizer ===\n")

    # --- Option 1: pass folder as argument when launching ---
    # Usage: python convert_images.py "C:/my/images" "_kindle"
    if len(sys.argv) >= 2:
        input_dir = sys.argv[1].strip()
        suffix = sys.argv[2].strip() if len(sys.argv) >= 3 else "_kindle"
    else:
        # --- Option 2: hardcode paths here for IDE "run button" use ---
        input_dir = ""   # ← paste your folder path here, e.g. r"C:\Users\you\images"
        suffix    = ""   # ← e.g. "_kindle"

        # --- Option 3: fall back to interactive prompts (works in terminal) ---
        if not input_dir:
            while True:
                input_dir = input("Enter the path to the folder with PNG images: ").strip()
                if os.path.isdir(input_dir):
                    break
                print(f"  Folder not found: '{input_dir}'. Please try again.\n")

        if not suffix:
            suffix = input("Enter a suffix for the output folder (e.g. '_kindle'): ").strip() or "_kindle"

    input_dir = os.path.normpath(input_dir)

    if not os.path.isdir(input_dir):
        print(f"Error: folder not found: '{input_dir}'")
        sys.exit(1)

    if not suffix:
        suffix = "_kindle"

    output_dir = input_dir + suffix
    print(f"Output folder will be: {output_dir}\n")
    process_images(input_dir, output_dir)
