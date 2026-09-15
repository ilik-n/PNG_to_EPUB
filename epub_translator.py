"""
epub_translator.py
------------------
Translates an EPUB book sentence-by-sentence, interleaving the original
and translated text. Output is a new EPUB in the same folder as the source.

Dependencies:
    pip install ebooklib beautifulsoup4 nltk deep-translator

Usage:
    python epub_translator.py
"""

import os
import re
import sys
import time
import threading
import zipfile

import nltk
from bs4 import BeautifulSoup
from ebooklib import epub, ITEM_DOCUMENT
from deep_translator import GoogleTranslator

# ── Language constants ────────────────────────────────────────────────────────
SOURCE_LANG = "da"   # Danish  │ Change to any Google Translate language code
TARGET_LANG = "en"   # English │ e.g. "de", "fr", "he", "es" …
# ─────────────────────────────────────────────────────────────────────────────

# How long to wait between translation requests (seconds) to avoid rate-limiting
TRANSLATE_DELAY = 0.3

# Languages that should be rendered right-to-left
RTL_LANGS = {"he", "ar", "fa", "ur", "yi"}

# Danish abbreviations that should NOT trigger sentence splits
DANISH_ABBREVIATIONS = [
    "bl.a", "fx", "ca", "dvs", "osv", "pga", "ifm", "ift",
    "mfl", "mv", "jf", "dr", "mr", "mrs", "prof", "nr", "st",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "okt", "nov", "dec",
]


# ── Spinner ───────────────────────────────────────────────────────────────────
class Spinner:
    """Simple terminal spinner to show progress during translation."""
    FRAMES = ["|", "/", "-", "\\"]

    def __init__(self, message: str = "  Translating"):
        self.message = message
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        i = 0
        while not self._stop_event.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r{self.message} {frame}")
            sys.stdout.flush()
            time.sleep(0.12)
            i += 1

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, final_message: str = ""):
        self._stop_event.set()
        self._thread.join()
        sys.stdout.write(f"\r{final_message}\n")
        sys.stdout.flush()


# ── NLTK setup ────────────────────────────────────────────────────────────────
def ensure_nltk():
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        print("📦 Downloading NLTK punkt tokenizer...")
        nltk.download("punkt_tab", quiet=True)


# ── Sentence splitting ────────────────────────────────────────────────────────
def split_sentences(text: str) -> list[str]:
    """Split text into sentences, respecting Danish abbreviations."""
    masked = text
    placeholders = {}
    for i, abbr in enumerate(DANISH_ABBREVIATIONS):
        placeholder = f"ABBR{i}ABBR"
        pattern = re.compile(re.escape(abbr) + r"\.", re.IGNORECASE)
        masked, count = pattern.subn(placeholder, masked)
        if count:
            placeholders[placeholder] = abbr + "."

    sentences = nltk.sent_tokenize(masked, language="danish")

    restored = []
    for s in sentences:
        for placeholder, original in placeholders.items():
            s = s.replace(placeholder, original)
        s = s.strip()
        if s:
            restored.append(s)

    return restored


# ── Translation ───────────────────────────────────────────────────────────────
def translate_sentence(text: str, translator: GoogleTranslator) -> str:
    """Translate a single sentence, with basic retry logic."""
    text = text.strip()
    if not text:
        return ""
    for attempt in range(3):
        try:
            result = translator.translate(text)
            time.sleep(TRANSLATE_DELAY)
            return result or text
        except Exception as e:
            if attempt == 2:
                print(f"  ⚠️  Translation failed after 3 attempts: {e}")
                return text
            time.sleep(1.5)
    return text


# ── HTML transformation ───────────────────────────────────────────────────────
def transform_chapter_html(html_content: str, translator: GoogleTranslator) -> str:
    """
    For every <p> in the chapter, split into sentences and replace with:
      <p>Danish sentence</p>
      <p><em>Translated sentence</em></p>
    Headings and non-paragraph elements are left untouched.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    rtl_target = TARGET_LANG.lower() in RTL_LANGS

    for p in soup.find_all("p"):
        raw_text = p.get_text(separator=" ").strip()
        if not raw_text:
            continue

        sentences = split_sentences(raw_text)
        new_tags = []

        for sentence in sentences:
            # Original sentence — tight bottom margin to pull translation close
            orig_tag = soup.new_tag("p", style="margin-bottom: 0.1em;")
            orig_tag.string = sentence
            new_tags.append(orig_tag)

            # Translated sentence — no top margin, generous bottom to separate from next pair
            trans_text = translate_sentence(sentence, translator)
            trans_style = "margin-top: 0; margin-bottom: 1.2em;"
            if rtl_target:
                trans_style += " direction: rtl; text-align: right;"
            trans_p = soup.new_tag("p", style=trans_style)
            if rtl_target:
                trans_p["dir"] = "rtl"
            em_tag = soup.new_tag("em")
            em_tag.string = trans_text
            trans_p.append(em_tag)
            new_tags.append(trans_p)

        for tag in new_tags:
            p.insert_before(tag)
        p.decompose()

    return str(soup)


# ── EPUB validation ───────────────────────────────────────────────────────────
def validate_epub(path: str) -> tuple[bool, str]:
    """Basic structural validation of the EPUB file."""
    if not os.path.isfile(path):
        return False, "File not found."
    if not path.lower().endswith(".epub"):
        return False, "File does not have an .epub extension."
    if not zipfile.is_zipfile(path):
        return False, "File is not a valid ZIP/EPUB archive."
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if not any("content.opf" in n or "package.opf" in n for n in names):
            return False, "No OPF manifest found — may not be a standard EPUB."
    return True, "OK"


# ── Chapter listing ───────────────────────────────────────────────────────────
def get_chapters(book: epub.EpubBook) -> list[tuple[int, epub.EpubHtml]]:
    """Return ordered list of (index, item) for readable HTML chapters."""
    spine_ids = [item_id for item_id, _ in book.spine]
    chapters = []
    for item_id in spine_ids:
        item = book.get_item_with_id(item_id)
        if item and isinstance(item, epub.EpubHtml):
            chapters.append(item)
    if not chapters:
        chapters = list(book.get_items_of_type(ITEM_DOCUMENT))
    return list(enumerate(chapters, start=1))


def chapter_title(item: epub.EpubHtml) -> str:
    """Extract a readable title from a chapter's HTML."""
    soup = BeautifulSoup(item.get_content(), "html.parser")
    heading = soup.find(["h1", "h2", "h3"])
    return heading.get_text().strip() if heading else item.get_name()


def estimate_sentences(item: epub.EpubHtml) -> int:
    soup = BeautifulSoup(item.get_content(), "html.parser")
    text = " ".join(p.get_text() for p in soup.find_all("p"))
    return len(split_sentences(text))


# ── Output path ───────────────────────────────────────────────────────────────
def build_output_path(source_path: str, lang: str, single_chapter: bool, chapter_num: int = None) -> str:
    folder = os.path.dirname(os.path.abspath(source_path))
    basename = os.path.splitext(os.path.basename(source_path))[0]
    suffix = f"_ch{chapter_num}" if single_chapter else ""
    return os.path.join(folder, f"{basename}{suffix}_{lang}_translated.epub")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ensure_nltk()

    print("=" * 60)
    print("  EPUB Bilingual Translator")
    print(f"  {SOURCE_LANG.upper()} → {TARGET_LANG.upper()}")
    print("=" * 60)

    # 1. Get EPUB path
    epub_path = input("\n📂 Enter path to EPUB file: ").strip().strip('"').strip("'")

    # 2. Validate
    print("\n🔍 Validating EPUB...")
    valid, msg = validate_epub(epub_path)
    if not valid:
        print(f"  ❌ {msg}")
        return
    print("  ✅ Valid EPUB")

    # 3. Load book and list ALL entries
    book = epub.read_epub(epub_path)
    chapters = get_chapters(book)

    if not chapters:
        print("  ❌ No readable chapters found.")
        return

    print(f"\n📖 All entries found ({len(chapters)} total):\n")
    for num, item in chapters:
        print(f"  {num:>3}. {chapter_title(item)}")

    # 4. Ask where real content begins (skip front matter / TOC / images)
    print()
    while True:
        try:
            first_ch = int(input("⚙️  Enter the number of the first chapter to translate (earlier entries will be skipped): ").strip())
            if 1 <= first_ch <= len(chapters):
                break
            print(f"   Please enter a number between 1 and {len(chapters)}.")
        except ValueError:
            print("   Please enter a valid number.")

    # Confirm the filtered list
    translatable = chapters[first_ch - 1:]
    print(f"\n✅ Translatable chapters ({len(translatable)}):\n")
    for num, item in translatable:
        print(f"  {num:>3}. {chapter_title(item)}")

    # 5. Scope: one chapter or full book
    print()
    scope = input("🌐 Translate one chapter or the whole book? (c / b): ").strip().lower()
    single_chapter = scope != "b"

    selected_indices = []
    if single_chapter:
        while True:
            try:
                ch_num = int(input(f"   Which chapter? ({first_ch}–{len(chapters)}): ").strip())
                if first_ch <= ch_num <= len(chapters):
                    selected_indices = [ch_num - 1]
                    break
                print(f"   Please enter a number between {first_ch} and {len(chapters)}.")
            except ValueError:
                print("   Please enter a valid number.")
    else:
        selected_indices = list(range(first_ch - 1, len(chapters)))

    # 6. Estimate and confirm
    total_sentences = sum(estimate_sentences(chapters[i][1]) for i in selected_indices)
    est_minutes = (total_sentences * (TRANSLATE_DELAY + 0.2)) / 60
    scope_label = f"chapter {selected_indices[0] + 1}" if single_chapter else f"all chapters ({first_ch}–{len(chapters)})"

    print(f"\n📊 Summary:")
    print(f"  Scope       : {scope_label}")
    print(f"  Sentences   : ~{total_sentences}")
    print(f"  Est. time   : ~{est_minutes:.1f} minutes")
    print(f"  Output lang : {TARGET_LANG.upper()}")

    confirm = input("\n▶️  Proceed? (y / n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    # 7. Translate
    translator = GoogleTranslator(source=SOURCE_LANG, target=TARGET_LANG)
    output_book = epub.EpubBook()

    output_book.set_identifier(
        book.get_metadata("DC", "identifier")[0][0]
        if book.get_metadata("DC", "identifier") else "translated"
    )
    output_book.set_title(
        (book.get_metadata("DC", "title") or [["Translated"]])[0][0]
    )
    output_book.set_language(TARGET_LANG)

    spine_items = []

    for i, (num, item) in enumerate(chapters):
        if i in selected_indices:
            title = chapter_title(item)
            print(f"\n📝 Translating: {title}")

            spinner = Spinner(f"  Working")
            spinner.start()
            new_html = transform_chapter_html(item.get_content().decode("utf-8"), translator)
            spinner.stop(f"  ✅ Done")
            new_item = epub.EpubHtml(
                title=item.title,
                file_name=item.get_name(),
                lang=TARGET_LANG,
            )
            new_item.set_content(new_html.encode("utf-8"))
            output_book.add_item(new_item)
            spine_items.append(new_item)

    # 8. Build EPUB structure
    output_book.add_item(epub.EpubNcx())
    output_book.add_item(epub.EpubNav())
    output_book.spine = ["nav"] + spine_items
    output_book.toc = [
        epub.Link(item.get_name(), item.title or "Chapter", "")
        for item in spine_items
    ]

    # 9. Write output
    ch_num_for_name = selected_indices[0] + 1 if single_chapter else None
    output_path = build_output_path(epub_path, TARGET_LANG, single_chapter, ch_num_for_name)
    epub.write_epub(output_path, output_book)

    print(f"\n✅ Done! Saved to:\n   {output_path}\n")


if __name__ == "__main__":
    main()
