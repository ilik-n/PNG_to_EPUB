#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PNG to EPUB Converter
Converts a folder of numbered PNG images to an EPUB file using OCR
"""

import os
import re
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

    def ocr_image(self, image_path, language='heb'):
        """Perform OCR on a single image"""
        print(f"  OCR processing: {image_path.name}...")

        # Open image
        img = Image.open(image_path)

        # Perform OCR with specified language
        # 'heb' = Hebrew, 'dan' = Danish, 'eng' = English
        text = pytesseract.image_to_string(img, lang=language)

        return text

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
                    chapter_title = current_chapter_text[0].split('\n')[0].strip()
                    chapter_item = self.save_chapter(
                        chapter_title, current_chapter_text, current_chapter_num, book_language)
                    chapter_items.append(chapter_item)
                    all_spine_items.append(chapter_item)
                    current_chapter_num += 1

                # Start new chapter
                next_chapter_title = text.split('\n')[0].strip()
                print(f"  → Starting new chapter: {next_chapter_title}")
                current_chapter_text = [text]
                next_chapter_idx += 1
            else:
                # Continue current chapter
                current_chapter_text.append(text)

        # Save the last chapter
        if current_chapter_text:
            chapter_title = current_chapter_text[0].split('\n')[0].strip()
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
        # Skip the first paragraph if it matches the title (avoid duplication)
        if all_paragraphs and all_paragraphs[0].strip() == title.strip():
            all_paragraphs = all_paragraphs[1:]
        paragraphs = ''.join(f'<p>{para}</p>' for para in all_paragraphs)

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
                p {{
                    text-align: justify;
                    line-height: 1.6;
                }}
            </style>
        </head>
        <body>
            <h1>{title}</h1>
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
