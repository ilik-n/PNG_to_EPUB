## bilingual_epub.py

Turns a Danish EPUB into a bilingual one for reading practice: every Danish
sentence is followed on the next line by a small grey, plain English
translation from Gemini. When you hit an unknown word, glance at the next line
instead of opening a dictionary.

A Gemini-based alternative to `epub_translator.py` (which uses the free Google
Translate endpoint, and can get rate-limited on long books). This one is
non-interactive, caches its work, caps spend, and verifies its own output.

### Dependencies

```bash
pip install lxml
```

Nothing else: the Gemini call uses the standard library (`urllib`).

### Usage

```bash
export GEMINI_API_KEY=...        # read from the environment only; never put it in the script
python3 bilingual_epub.py book.epub book_bilingual.epub
```

| Flag | Meaning |
|---|---|
| `--budget 1.00` | Hard stop in USD. The run halts and prints a loud COST ALERT if cumulative cost passes it. |
| `--max-sentences N` | Translate only N new sentences (cheap test run; no EPUB is built). |
| `--batch 40` | Sentences per API call. |

Translations and the cost ledger are cached in `<output>.cache/`
(`translations.json`, `cost.json`), so re-running never pays twice. If a run
stops (budget, network), just run it again. Do not commit those cache folders.

### How it works

1. Reads the chapter list from the EPUB's own spine (no hardcoded file names).
2. Splits each heading/paragraph into sentences while preserving inline markup
   (italics, links): sentences are cut out of the original runs instead of
   flattening to text. Handles quotes after a full stop (`.”`) and Danish
   abbreviations (`dvs.`, `f.eks.`, `osv.` ...). Blocks containing images etc.
   are left untouched.
3. Translates unique sentences in batches (JSON in, JSON out, temperature 0);
   if the returned count is wrong, the batch is bisected.
4. Rebuilds each block as `Danish<br/><em class="tr" style="...">English</em>`
   and writes the EPUB in the original member order (`mimetype` first, stored).
5. Self-verifies: strict XML parse, zip integrity, Danish text identical to the
   original, exactly one translation per sentence.

### Notes

- Model: `gemini-3.1-flash-lite` ($0.25 in / $1.50 out per 1M tokens, set as
  `RATE_IN`/`RATE_OUT` in the script, so update them if prices change).
  A 700-sentence, 5-chapter book cost about USD 0.03.
- The prompt asks for a literal, word-order-preserving translation so words are
  easy to match, so the English is slightly stilted on purpose.
- The book text is sent to Google's Gemini API.
- Saved reading positions (e.g. `calibre_bookmarks.txt`) are copied unchanged
  and may be off in the longer bilingual book.
- Tested on a Danish popular-science EPUB; inline-markup handling was tested on
  a synthetic paragraph only. To translate another language pair, edit
  `SYSTEM_PROMPT`.
