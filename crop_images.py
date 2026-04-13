import os
import sys
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

# ── Globals ──────────────────────────────────────────────────────────────────
crop_box   = None   # (x1, y1, x2, y2) in ORIGINAL image pixels
start_xy   = None
rect_id    = None
scale      = 1.0

# ── Canvas interaction ────────────────────────────────────────────────────────
def on_press(event):
    global start_xy, rect_id
    start_xy = (event.x, event.y)
    if rect_id:
        canvas.delete(rect_id)

def on_drag(event):
    global rect_id
    if rect_id:
        canvas.delete(rect_id)
    rect_id = canvas.create_rectangle(
        start_xy[0], start_xy[1], event.x, event.y,
        outline="red", width=2
    )

def on_release(event):
    global crop_box
    x1, y1 = start_xy
    x2, y2 = event.x, event.y
    # Normalize so x1<x2, y1<y2
    x1, x2 = sorted([x1, x2])
    y1, y2 = sorted([y1, y2])
    # Convert canvas coords back to original image coords
    crop_box = (
        int(x1 / scale),
        int(y1 / scale),
        int(x2 / scale),
        int(y2 / scale),
    )
    lbl_status.config(
        text=f"Crop region: {crop_box[0]},{crop_box[1]}  →  {crop_box[2]},{crop_box[3]}   (original px)"
    )

def confirm_crop():
    if not crop_box:
        messagebox.showwarning("No selection", "Please draw a crop rectangle first.")
        return
    root.destroy()

# ── Selection window ──────────────────────────────────────────────────────────
def select_crop_region(img_path):
    global canvas, lbl_status, root, scale

    root = tk.Tk()
    root.title("Draw crop rectangle — then click Confirm")

    img = Image.open(img_path)
    orig_w, orig_h = img.size

    # Fit image to max 900×700 display area
    max_w, max_h = 900, 700
    scale = min(max_w / orig_w, max_h / orig_h, 1.0)
    disp_w = int(orig_w * scale)
    disp_h = int(orig_h * scale)

    disp_img  = img.resize((disp_w, disp_h), Image.LANCZOS)
    tk_img    = ImageTk.PhotoImage(disp_img)

    canvas = tk.Canvas(root, width=disp_w, height=disp_h, cursor="crosshair")
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=tk_img)
    canvas.bind("<ButtonPress-1>",   on_press)
    canvas.bind("<B1-Motion>",       on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    lbl_status = tk.Label(root, text="Click and drag to select crop area", fg="gray")
    lbl_status.pack(pady=4)

    btn = tk.Button(root, text="✔  Confirm crop", command=confirm_crop,
                    bg="#2e7d32", fg="white", padx=12, pady=6)
    btn.pack(pady=6)

    root.mainloop()

# ── Preview window ────────────────────────────────────────────────────────────
def preview_crop(img_path, box):
    with Image.open(img_path) as img:
        cropped = img.crop(box)

    root2 = tk.Tk()
    root2.title("Preview — close to cancel, click Proceed to batch-process")

    max_w, max_h = 900, 700
    cw, ch = cropped.size
    sc = min(max_w / cw, max_h / ch, 1.0)
    disp = cropped.resize((int(cw * sc), int(ch * sc)), Image.LANCZOS)
    tk_img = ImageTk.PhotoImage(disp)

    tk.Label(root2, image=tk_img).pack()
    tk.Label(root2, text=f"Cropped size: {cw} × {ch} px", fg="gray").pack(pady=4)

    proceed = [False]

    def do_proceed():
        proceed[0] = True
        root2.destroy()

    tk.Button(root2, text="✔  Looks good — process all images", command=do_proceed,
              bg="#1565c0", fg="white", padx=12, pady=6).pack(pady=8)

    root2.mainloop()
    return proceed[0]

# ── Batch processing ──────────────────────────────────────────────────────────
def process_images(input_folder, output_folder, box):
    os.makedirs(output_folder, exist_ok=True)

    png_files = [f for f in os.listdir(input_folder) if f.lower().endswith(".png")]
    if not png_files:
        print("No PNG files found.")
        return

    print(f"\nCrop box: {box}")
    print(f"Processing {len(png_files)} image(s)...\n")

    for filename in sorted(png_files):
        src = os.path.join(input_folder, filename)
        dst = os.path.join(output_folder, filename)
        try:
            with Image.open(src) as img:
                cropped = img.crop(box)
                cropped.save(dst, "PNG", optimize=True)
            orig_kb    = os.path.getsize(src) / 1024
            new_kb     = os.path.getsize(dst) / 1024
            print(f"  ✓ {filename}  ({orig_kb:.1f} KB → {new_kb:.1f} KB)")
        except Exception as e:
            print(f"  ✗ {filename}: {e}")

    print(f"\nDone! Saved to: {output_folder}")

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Interactive Crop & Batch Process ===\n")

    if len(sys.argv) >= 2:
        input_dir = sys.argv[1].strip()
        suffix    = sys.argv[2].strip() if len(sys.argv) >= 3 else "_cropped"
    else:
        input_dir = ""
        suffix    = ""

        if not input_dir:
            while True:
                input_dir = input("Enter the path to the folder with PNG images: ").strip()
                if os.path.isdir(input_dir):
                    break
                print(f"  Folder not found: '{input_dir}'. Please try again.\n")

        if not suffix:
            suffix = input("Enter a suffix for the output folder (e.g. '_cropped'): ").strip() or "_cropped"

    input_dir  = os.path.normpath(input_dir)
    output_dir = input_dir + suffix

    png_files = sorted([f for f in os.listdir(input_dir) if f.lower().endswith(".png")])
    if not png_files:
        print("No PNG files found in that folder.")
        sys.exit(1)

    first_image = os.path.join(input_dir, png_files[0])
    print(f"\nOpening first image for crop selection: {png_files[0]}\n")

    # Step 1 — draw crop rectangle
    select_crop_region(first_image)

    if not crop_box:
        print("No crop region selected. Exiting.")
        sys.exit(0)

    # Step 2 — preview result
    proceed = preview_crop(first_image, crop_box)
    if not proceed:
        print("Cancelled. No images were saved.")
        sys.exit(0)

    # Step 3 — batch process all
    process_images(input_dir, output_dir, crop_box)
