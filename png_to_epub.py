#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PNG to EPUB Converter
Converts a folder of numbered PNG images to an EPUB file using OCR
"""

import os
import re
import math
from pathlib import Path
import pytesseract
from PIL import Image
from ebooklib import epub
import cv2
import numpy as np

# Configure Tesseract path (adjust if your installation is different)
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


class PNGtoEPUB:
    # Language configuration for OCR
    LANGUAGES = {
        '1': ('English', 'eng'),
        '2': ('Danish', 'dan'),
        '3': ('Hebrew', 'heb'),
    }

    # ── Italic-paragraph detection/correction (Hebrew only) ────────────────
    # A paragraph whose mean OCR confidence falls below this is treated as
    # sheared (italic) text, which Tesseract's upright-trained 'heb' model
    # misreads. Tuned against real samples: regular paragraphs average
    # ~90, italic paragraphs average ~55.
    ITALIC_PARAGRAPH_MEAN_THRESHOLD = 75
    # This book's citations are consistently formatted as
    # "Name Name (description): <italic quote>" -- confidence and word
    # height turned out too noisy to reliably tell a genuine bold name from
    # a few lucky high-confidence/tall words inside a fully italic
    # paragraph (verified against real OCR data). The parenthesis is a much
    # more reliable marker of where the name ends, so a flagged paragraph's
    # leading prefix is everything on its first line before the first word
    # containing '(', searched only within this many leading words (to
    # avoid matching a stray parenthesis deep inside actual quote text).
    ITALIC_PREFIX_SEARCH_WORDS = 6
    # Shear angles (degrees) swept to find the correction that best
    # straightens a run's glyphs back to something the 'heb' model can read.
    ITALIC_SHEAR_ANGLES = range(-30, 31, 2)

    # ── Centered-heading detection (Hebrew only) ────────────────────────────
    # Chapter titles and section headings in this book are centered, unlike
    # body text which is flush to one edge (right, for RTL) and ragged on
    # the other. A line is "centered" if both its left and right margins are
    # clearly wider than body text's tight ~20-30px edge margin, and roughly
    # balanced -- verified against real pages: heading margins were ~170-335
    # px and within ~4% of each other, vs. body text's one ~20-30px margin
    # against a ragged, often much larger, other margin.
    HEADING_MIN_MARGIN = 60
    HEADING_MARGIN_BALANCE = 0.5
    # Headings (chapter titles, section headings) are short; cap how many
    # lines a paragraph can have and still be considered one.
    HEADING_MAX_LINES = 3
    # Non-printable sentinel prefixed to a heading paragraph's text so
    # downstream EPUB assembly can render it as <h1>/<h2> instead of <p>.
    # Chosen because it can never appear in real OCR output.
    HEADING_MARKER = '\x01HEADING\x01'

    def __init__(self, image_folder):
        self.image_folder = Path(image_folder)
        self.images = self.get_sorted_images()
        self.book = epub.EpubBook()

    def select_language(self):
        """Display language options and let user choose"""
        print("\nSelect language for OCR recognition:")
        print("-" * 40)
        for key, (name, code) in self.LANGUAGES.items():
            print(f"  {key}. {name}")
        print("-" * 40)

        while True:
            choice = input("Enter your choice (1-3): ").strip()
            if choice in self.LANGUAGES:
                language_name, language_code = self.LANGUAGES[choice]
                print(f"✓ Selected: {language_name}\n")
                return language_code
            else:
                print("✗ Invalid choice. Please enter 1, 2, or 3.")

    def select_mode(self):
        """Let user choose between text-only or images-only mode"""
        print("\nSelect conversion mode:")
        print("-" * 40)
        print("  1. Text only (with chapters)")
        print("  2. Images only (no chapters)")
        print("-" * 40)

        while True:
            choice = input("Enter your choice (1-2): ").strip()
            if choice == '1':
                print("✓ Selected: Text only with chapters\n")
                return 'text'
            elif choice == '2':
                print("✓ Selected: Images only\n")
                return 'images'
            else:
                print("✗ Invalid choice. Please enter 1 or 2.")

    def get_sorted_images(self):
        """Get all PNG files sorted numerically"""
        png_files = list(self.image_folder.glob('*.png'))
        # Sort by the number in filename (e.g., page_0001.png)
        png_files.sort(key=lambda x: int(re.findall(r'\d+', x.stem)[-1]))
        return png_files

    def strip_heading_marker(self, text):
        if text.startswith(self.HEADING_MARKER):
            return text[len(self.HEADING_MARKER):].strip()
        return text

    def extract_chapter_title(self, page_text):
        """First paragraph of a page's OCR text, with any heading marker stripped."""
        first_paragraph = page_text.split('\n')[0].strip()
        return self.strip_heading_marker(first_paragraph)

    def ocr_image(self, image_path, language='heb'):
        """Perform OCR on a single image"""
        print(f"  OCR processing: {image_path.name}...")

        # Open image
        img = Image.open(image_path)

        if language == 'heb':
            # Hebrew gets special handling: italic passages are sheared
            # glyphs that the upright-trained 'heb' model misreads, so we
            # detect and correct those runs instead of OCR'ing the page flat.
            return self.ocr_hebrew_page(img)

        # Perform OCR with specified language
        # 'dan' = Danish, 'eng' = English
        text = pytesseract.image_to_string(img, lang=language)

        return text

    def ocr_hebrew_page(self, img):
        """
        OCR a Hebrew page, detecting italic (sheared-font) paragraphs and
        re-recognizing them after straightening, wrapping recovered text in
        <em> so the EPUB preserves the source's italic formatting.

        Correction runs at paragraph granularity (Tesseract's block/par
        grouping), not per-word: a shear angle estimated from a handful of
        words is noisy, but estimated from a whole paragraph's worth of
        glyphs it converges on a single reliable angle (validated
        empirically -- a ~215-word italic paragraph swept cleanly to a
        single confidence peak matching regular-text confidence).
        """
        gray = img.convert('L')
        data = pytesseract.image_to_data(gray, lang='heb', output_type=pytesseract.Output.DICT)

        words = []
        for i in range(len(data['text'])):
            text = data['text'][i].strip()
            if not text:
                continue
            try:
                conf = int(data['conf'][i])
            except (ValueError, TypeError):
                conf = -1
            words.append({
                'text': text,
                'conf': conf,
                'left': data['left'][i],
                'top': data['top'][i],
                'width': data['width'][i],
                'height': data['height'][i],
                'line_key': (data['block_num'][i], data['par_num'][i], data['line_num'][i]),
                'par_key': (data['block_num'][i], data['par_num'][i]),
            })

        if not words:
            return ''

        # Group words into lines, then lines into paragraphs, preserving
        # Tesseract's reading order throughout.
        lines = []
        current_key = None
        current_line = []
        for w in words:
            if w['line_key'] != current_key:
                if current_line:
                    lines.append(current_line)
                current_line = []
                current_key = w['line_key']
            current_line.append(w)
        if current_line:
            lines.append(current_line)

        paragraphs = []
        current_par_key = None
        current_par_lines = []
        for line in lines:
            par_key = line[0]['par_key']
            if current_par_key is not None and par_key != current_par_key:
                paragraphs.append(current_par_lines)
                current_par_lines = []
            current_par_key = par_key
            current_par_lines.append(line)
        if current_par_lines:
            paragraphs.append(current_par_lines)

        page_width = gray.size[0]
        out_paragraphs = []
        for par_lines in paragraphs:
            if self.is_heading_paragraph(par_lines, page_width):
                heading_text = ' '.join(' '.join(w['text'] for w in line) for line in par_lines)
                out_paragraphs.append(f'{self.HEADING_MARKER}{heading_text}')
            else:
                out_paragraphs.append(self.render_hebrew_paragraph(gray, par_lines))
        return '\n\n'.join(out_paragraphs)

    def is_heading_paragraph(self, par_lines, page_width):
        """A paragraph is a heading if every one of its (few) lines is centered."""
        if len(par_lines) > self.HEADING_MAX_LINES:
            return False
        return all(self.is_line_centered(line, page_width) for line in par_lines)

    def is_line_centered(self, line_words, page_width):
        left = min(w['left'] for w in line_words)
        right = max(w['left'] + w['width'] for w in line_words)
        margin_l = left
        margin_r = page_width - right
        if margin_l < self.HEADING_MIN_MARGIN or margin_r < self.HEADING_MIN_MARGIN:
            return False
        smaller, larger = sorted((margin_l, margin_r))
        return smaller / larger >= self.HEADING_MARGIN_BALANCE

    def render_hebrew_paragraph(self, gray_page, par_lines):
        """
        Render one paragraph's text, correcting it as an italic block if its
        overall confidence says it's sheared. Handles the one case where a
        paragraph mixes styles: a bold, larger-font name introducing a
        quotation on the same line the italic quote begins on.
        """
        all_words = [w for line in par_lines for w in line]
        mean_conf = sum(w['conf'] for w in all_words) / len(all_words)

        if mean_conf >= self.ITALIC_PARAGRAPH_MEAN_THRESHOLD:
            return ' '.join(' '.join(w['text'] for w in line) for line in par_lines)

        # Flagged as italic. Peel off a leading regular-confidence prefix on
        # the first line only (e.g. a bold speaker name before a quote) --
        # that part must stay outside the <em> and isn't sheared.
        prefix_words, rest_of_first_line = self.split_leading_regular_prefix(par_lines)

        pieces = []
        if prefix_words:
            pieces.append(' '.join(w['text'] for w in prefix_words))

        italic_chunks = []
        if rest_of_first_line:
            italic_chunks.append(self.recover_italic_run(gray_page, rest_of_first_line))
        remaining_lines = par_lines[1:]
        if remaining_lines:
            remaining_words = [w for line in remaining_lines for w in line]
            italic_chunks.append(self.recover_italic_run(gray_page, remaining_words))

        if italic_chunks:
            pieces.append(f"<em>{' '.join(italic_chunks)}</em>")

        return ' '.join(pieces)

    def split_leading_regular_prefix(self, par_lines):
        """
        Split a flagged paragraph's first line into a leading speaker-name
        prefix and the rest, using this book's consistent citation format
        "Name Name (description): <quote>". Returns ([], first_line) if no
        '(' shows up within the first few words (no name on this paragraph).
        """
        first_line = par_lines[0]
        for i, w in enumerate(first_line[:self.ITALIC_PREFIX_SEARCH_WORDS]):
            if '(' in w['text']:
                if i == 0:
                    return [], first_line
                return first_line[:i], first_line[i:]
        return [], first_line

    def recover_italic_run(self, gray_page, run_words):
        """
        Crop a flagged run out of the page, sweep shear angles to find the
        one that best straightens its glyphs, and re-OCR the corrected crop.
        """
        left = min(w['left'] for w in run_words)
        top = min(w['top'] for w in run_words)
        right = max(w['left'] + w['width'] for w in run_words)
        bottom = max(w['top'] + w['height'] for w in run_words)

        # Padding based on a single line's height (not the whole block, which
        # may span many lines) -- a too-tight crop clips ascenders/descenders
        # and tanks OCR confidence on its own, independent of the shear issue.
        avg_line_height = sum(w['height'] for w in run_words) / len(run_words)
        pad_v = max(int(avg_line_height * 0.6), 10)
        pad_h = 10

        img_w, img_h = gray_page.size
        crop = gray_page.crop((
            max(0, left - pad_h),
            max(0, top - pad_v),
            min(img_w, right + pad_h),
            min(img_h, bottom + pad_v),
        ))

        best_angle = 0
        best_conf = -1
        best_img = crop
        for angle in self.ITALIC_SHEAR_ANGLES:
            sheared = self.shear_image(crop, angle)
            data = pytesseract.image_to_data(sheared, lang='heb', output_type=pytesseract.Output.DICT)
            confs = [int(c) for c in data['conf'] if int(c) >= 0]
            mean_conf = sum(confs) / len(confs) if confs else 0
            if mean_conf > best_conf:
                best_conf = mean_conf
                best_angle = angle
                best_img = sheared

        text = pytesseract.image_to_string(best_img, lang='heb').strip()
        text = ' '.join(text.split())
        if not text:
            # Fall back to the original (garbled) transcription rather than
            # dropping the run entirely.
            text = ' '.join(w['text'] for w in run_words)
        return text

    @staticmethod
    def shear_image(img, angle_deg):
        """Shear a grayscale image horizontally by angle_deg to straighten italic glyphs."""
        if angle_deg == 0:
            return img
        w, h = img.size
        shear = math.tan(math.radians(angle_deg))
        new_w = w + int(abs(shear) * h)
        offset = int(shear * h) if shear > 0 else 0
        coeffs = (1, -shear, offset, 0, 1, 0)
        # BILINEAR (not the default NEAREST) matters here: nearest-neighbor
        # resampling leaves jagged, aliased diagonal strokes after the shear,
        # which blurs exactly the fine shape distinction that separates
        # visually similar letter pairs like ג/נ -- verified empirically,
        # this measurably cut that specific confusion without hurting
        # confidence.
        return img.transform((new_w, h), Image.AFFINE, coeffs, fillcolor=255, resample=Image.BILINEAR)

    def create_epub_text_mode(self, output_filename, book_title, book_author, book_language):
        """Create EPUB with text only and chapters"""
        print(f"\nFound {len(self.images)} images total")

        # Build a map of filename-number -> image_path for easy lookup
        image_map = {int(re.findall(r'\d+', p.stem)[-1]): p for p in self.images}
        page_numbers = sorted(image_map.keys())
        min_page = page_numbers[0]
        max_page = page_numbers[-1]

        # Ask for starting page (using actual filename numbers)
        while True:
            start_input = input(
                f"Enter the filename number where text begins (e.g. if first text page is page_0005.png, enter 5): ").strip()
            try:
                start_page = int(start_input)
                if start_page in image_map:
                    break
                else:
                    print(f"✗ No file found with number {start_page}. Available range: {min_page} to {max_page}")
            except ValueError:
                print("✗ Please enter a valid number")

        # Ask for chapter page numbers
        print(f"\nEnter the page numbers where chapters begin (comma-separated)")
        print(f"Example: {start_page}, {start_page+10}, {start_page+25}")
        chapter_input = input("Chapter pages: ").strip()

        chapter_pages = []
        if chapter_input:
            try:
                chapter_pages = [int(x.strip())
                                 for x in chapter_input.split(',')]
                chapter_pages.sort()
                print(f"✓ Chapters will begin at pages: {chapter_pages}")
            except ValueError:
                print("✗ Invalid input. Proceeding with no chapter breaks.")
                chapter_pages = [start_page]
        else:
            chapter_pages = [start_page]

        print("\nStarting OCR and EPUB creation...\n")

        # Track all items for spine (in order)
        all_spine_items = []

        # Process front matter pages as images (all files with number < start_page)
        front_pages = [image_map[n] for n in page_numbers if n < start_page]
        if front_pages:
            for fm_idx, image_path in enumerate(front_pages, 1):
                print(f"Processing front matter image {fm_idx}/{len(front_pages)}: {image_path.name}")

                # Read image file
                with open(image_path, 'rb') as img_file:
                    img_data = img_file.read()

                # Add image to book
                img_item = epub.EpubItem(
                    uid=f"fm_image_{fm_idx}",
                    file_name=f"images/{image_path.name}",
                    media_type="image/png",
                    content=img_data
                )
                self.book.add_item(img_item)

                # Create a page for this image (no title shown)
                page = epub.EpubHtml(
                    title='',
                    file_name=f'fm_page_{fm_idx}.xhtml',
                    lang=book_language
                )
                page.set_content(f'''<?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE html>
                <html xmlns="http://www.w3.org/1999/xhtml">
                <head>
                    <meta charset="UTF-8"/>
                    <title></title>
                    <style>
                        html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; }}
                        div {{ width: 100%; height: 100%; }}
                        img {{ width: 100%; height: 100%; object-fit: contain; }}
                    </style>
                </head>
                <body>
                    <div><img src="images/{image_path.name}" alt=""/></div>
                </body>
                </html>'''.encode('utf-8'))
                self.book.add_item(page)
                all_spine_items.append(page)

        # Process main content with chapters
        current_chapter_text = []
        current_chapter_num = 1
        next_chapter_idx = 0
        chapter_items = []

        text_pages = [image_map[n] for n in page_numbers if n >= start_page]
        for image_path in text_pages:
            page_num = int(re.findall(r'\d+', image_path.stem)[-1])
            print(f"Processing page {page_num}/{max_page}: {image_path.name}")

            # OCR the image
            text = self.ocr_image(image_path, language=book_language)

            if not text.strip():
                print("  Warning: No text detected, skipping...")
                continue

            # Check if this page starts a new chapter
            if next_chapter_idx < len(chapter_pages) and page_num == chapter_pages[next_chapter_idx]:
                # Save previous chapter if it has content
                if current_chapter_text:
                    chapter_title = self.extract_chapter_title(current_chapter_text[0])
                    chapter_item = self.save_chapter(
                        chapter_title, current_chapter_text, current_chapter_num, book_language)
                    chapter_items.append(chapter_item)
                    all_spine_items.append(chapter_item)
                    current_chapter_num += 1

                # Start new chapter
                next_chapter_title = self.extract_chapter_title(text)
                print(f"  → Starting new chapter: {next_chapter_title}")
                current_chapter_text = [text]
                next_chapter_idx += 1
            else:
                # Continue current chapter
                current_chapter_text.append(text)

        # Save the last chapter
        if current_chapter_text:
            chapter_title = self.extract_chapter_title(current_chapter_text[0])
            chapter_item = self.save_chapter(chapter_title, current_chapter_text, current_chapter_num, book_language)
            chapter_items.append(chapter_item)
            all_spine_items.append(chapter_item)

        # Create table of contents (chapters only, no front matter images)
        self.book.toc = tuple(chapter_items)

        # Add navigation files
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())

        # Spine: front matter images first, then nav (TOC), then chapters
        spine_front = [i for i in all_spine_items if i not in chapter_items]
        spine_chapters = [i for i in all_spine_items if i in chapter_items]
        self.book.spine = spine_front + ['nav'] + spine_chapters

        # Write EPUB file
        output_path = self.image_folder / output_filename
        epub.write_epub(output_path, self.book, {})

        print(f"\n✓ EPUB created successfully!")
        print(f"  Location: {output_path}")
        print(f"  Total chapters: {current_chapter_num}")

        return output_path

    def create_epub_images_mode(self, output_filename, book_title, book_author, book_language):
        """Create EPUB with images only (no OCR, no chapters)"""
        print(f"\nFound {len(self.images)} images to include")
        print("Creating EPUB with images...\n")

# List to collect all pages
        pages = []
        for idx, image_path in enumerate(self.images, 1):
            print(f"Adding image {idx}/{len(self.images)}: {image_path.name}")

            # Read image file
            with open(image_path, 'rb') as img_file:
                img_data = img_file.read()

            # Create EPUB image item
            img_item = epub.EpubItem(
                uid=f"image_{idx}",
                file_name=f"images/{image_path.name}",
                media_type="image/png",
                content=img_data
            )
            self.book.add_item(img_item)

# Create a separate page for this image
            page = epub.EpubHtml(
                title=f'Page {idx}',
                file_name=f'page_{idx}.xhtml',
                lang=book_language
            )
            page.set_content(f'''<?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE html>
            <html xmlns="http://www.w3.org/1999/xhtml">
            <head>
                <meta charset="UTF-8"/>
                <meta name="viewport" content="width=device-width, height=device-height"/>
                <title>Page {idx}</title>
                <style>
                    html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; }}
                    div {{ width: 100%; height: 100%; }}
                    img {{ width: 100%; height: 100%; object-fit: contain; }}
                </style>
            </head>
            <body>
                <div><img src="images/{image_path.name}" alt="Page {idx}"/></div>
            </body>
            </html>'''.encode('utf-8'))
            self.book.add_item(page)
            pages.append(page)

        # Create table of contents
        self.book.toc = tuple(pages)

        # Add navigation files
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())

        # Create spine (reading order)
        self.book.spine = pages

        # Write EPUB file
        output_path = self.image_folder / output_filename
        epub.write_epub(output_path, self.book, {})

        print(f"\n✓ EPUB created successfully!")
        print(f"  Location: {output_path}")
        print(f"  Total images: {len(self.images)}")

        return output_path

    def create_epub(self, output_filename):
        """Main function to create EPUB from images"""
        # Set metadata
        book_title = input("Enter book title: ").strip() or "Book"
        book_author = input("Enter author name: ").strip() or "Unknown"

        # Select mode
        mode = self.select_mode()

# Select language only for text mode
        if mode == 'text':
            book_language = self.select_language()
        else:
            book_language = 'en'

        # Set book metadata
        self.book.set_identifier(f'book_{book_title.replace(" ", "_")}')
        self.book.set_title(book_title)
        self.book.set_language(book_language)
        self.book.add_author(book_author)
        if book_language == 'heb':
            # Sets page-progression-direction="rtl" in the OPF spine -- some
            # readers use this (not just per-page CSS/dir) to decide overall
            # book reading direction and default text alignment.
            self.book.set_direction('rtl')

        # Process based on mode
        if mode == 'text':
            return self.create_epub_text_mode(output_filename, book_title, book_author, book_language)
        else:
            return self.create_epub_images_mode(output_filename, book_title, book_author, book_language)

    def save_chapter(self, title, text_pages, chapter_num, language='heb'):
        """Save a chapter to the EPUB book with proper text direction"""
        # Combine all pages in this chapter
        full_text = '\n\n'.join(text_pages)

        # Determine text direction
        text_direction = 'rtl' if language == 'heb' else 'ltr'

        # Create chapter
        chapter = epub.EpubHtml(
            title=title,
            file_name=f'chapter_{chapter_num}.xhtml',
            lang=language
        )

        # Format content with proper RTL support and encoding
        all_paragraphs = [para for para in full_text.split('\n\n') if para.strip()]
        # Skip the first paragraph if it matches the title (avoid duplication) --
        # it may carry the heading marker (see ocr_hebrew_page), so compare
        # against the stripped form.
        if all_paragraphs and self.strip_heading_marker(all_paragraphs[0]) == title.strip():
            all_paragraphs = all_paragraphs[1:]
        # Explicit dir="" on every block, not just <html> -- several e-reader
        # rendering engines don't reliably apply CSS `direction` for bidi
        # paragraph embedding (which governs where punctuation lands) and
        # need the DOM dir attribute directly on each block element.
        rendered_paragraphs = []
        for para in all_paragraphs:
            if para.startswith(self.HEADING_MARKER):
                rendered_paragraphs.append(f'<h2 dir="{text_direction}">{self.strip_heading_marker(para)}</h2>')
            else:
                rendered_paragraphs.append(f'<p dir="{text_direction}">{para}</p>')
        paragraphs = ''.join(rendered_paragraphs)

        content = f'''<?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE html>
        <html xmlns="http://www.w3.org/1999/xhtml" dir="{text_direction}">
        <head>
            <meta charset="UTF-8"/>
            <title>{title}</title>
            <style>
                body {{
                    font-family: Arial, 'Noto Sans Hebrew', 'Times New Roman', serif;
                    direction: {text_direction};
                    text-align: {'right' if text_direction == 'rtl' else 'left'};
                }}
                h1 {{
                    text-align: center;
                    font-size: 1.1em;
                    font-weight: normal;
                    margin-top: 40%;
                    margin-bottom: 1.5em;
                }}
                h2 {{
                    text-align: center;
                    font-size: 1.05em;
                    margin-top: 2em;
                    margin-bottom: 1em;
                }}
                p {{
                    text-align: justify;
                    line-height: 1.6;
                }}
            </style>
        </head>
        <body dir="{text_direction}">
            <h1 dir="{text_direction}">{title}</h1>
            {paragraphs}
        </body>
        </html>
        '''

        chapter.set_content(content.encode('utf-8'))

        # Add chapter to book
        self.book.add_item(chapter)
        print(f"  ✓ Saved chapter: {title}")
        return chapter


def main():
    print("=" * 60)
    print("PNG to EPUB Converter")
    print("=" * 60)
    print()

    # Ask user for folder path
    folder_path = input(
        "Enter the folder path containing PNG images: ").strip()

    # Remove quotes if user pasted path with quotes
    folder_path = folder_path.strip('"').strip("'")

    # Check if folder exists
    if not os.path.exists(folder_path):
        print(f"\n✗ Error: Folder not found: {folder_path}")
        input("\nPress Enter to exit...")
        return

    # Check if folder has PNG files
    png_files = list(Path(folder_path).glob('*.png'))
    if not png_files:
        print(f"\n✗ Error: No PNG files found in: {folder_path}")
        input("\nPress Enter to exit...")
        return

    print(f"\n✓ Found {len(png_files)} PNG files")

    # Ask for output filename
    output_filename = input(
        "\nEnter output EPUB filename (e.g., book.epub): ").strip()
    if not output_filename.endswith('.epub'):
        output_filename += '.epub'

    # Create converter and process
    try:
        converter = PNGtoEPUB(folder_path)
        converter.create_epub(output_filename)

        print("\n" + "=" * 60)
        print("Conversion complete!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Error during conversion: {e}")
        import traceback
        traceback.print_exc()

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
