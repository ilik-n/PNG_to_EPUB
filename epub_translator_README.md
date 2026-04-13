## epub_translator.py

Translates an EPUB book sentence-by-sentence, interleaving the original text with its translation. Output is a new EPUB file, ready to send to Kindle.

**Format:** Each Danish sentence is followed immediately by its translation in italics, with clear spacing between pairs. Chapter structure is preserved.

### Dependencies

```bash
pip install ebooklib beautifulsoup4 nltk deep-translator
```

### Configuration

At the top of the script, set the source and target languages using standard Google Translate language codes:

```python
SOURCE_LANG = "da"   # e.g. "da" Danish, "de" German, "fr" French …
TARGET_LANG = "en"   # e.g. "en" English, "he" Hebrew, "es" Spanish …
```

### Usage

```bash
python epub_translator.py
```

The script will prompt you through the following steps:

1. **Path** — enter the path to your EPUB file
2. **Validation** — the script checks the file is a valid EPUB
3. **Entry list** — all spine entries are listed (chapters, front matter, TOC)
4. **First chapter** — enter the number of the first real chapter to translate; earlier entries (cover images, TOC, etc.) are skipped
5. **Scope** — choose `c` to translate a single chapter (recommended for testing) or `b` for the whole book
6. **Summary** — estimated sentence count and translation time are shown before you confirm
7. **Output** — the translated EPUB is saved to the same folder as the source, named automatically: `originalname_ch5_en_translated.epub`

### Notes

- Translation is free via Google Translate (no API key needed)
- A small delay between requests avoids rate-limiting; longer books may take several minutes
- Danish abbreviations (`bl.a.`, `fx.`, `ca.` etc.) are handled to avoid false sentence splits
