# png_to_epub.py - future work

## Idea: pre-flight book-format questionnaire

Right now `ocr_hebrew_page`'s heuristics (italic detection, heading detection,
citation name-prefix) were tuned against one specific book. Before running a
new book, it'd help to have a short guided flow that captures its format up
front, so the right detectors get enabled/tuned instead of silently
mis-firing or missing things. Rough shape:

1. **Chapter headings** - are they centered? Bold/larger font? Does the
   chapter title span multiple lines (e.g. "Chapter N:" + name on its own
   line)? Sample page number to calibrate against.
2. **Sub-chapters / section headings** - do they exist? Same styling as
   chapter headings, or different (smaller, left-aligned, numbered)?
3. **Italics** - present at all? If yes: what do they mark (citations,
   emphasis, foreign words)? Sample page(s) with both a regular and an
   italic paragraph, to calibrate the shear-angle sweep and confidence
   threshold the same way we did manually this session.
4. **Citations / quotes format** - is there a consistent
   "Name (description): quote" pattern, or something else (no name, name on
   its own line, em-dash instead of parens)? This drives whether the
   parenthesis-based name-prefix rule applies as-is, needs a different
   marker, or should be skipped.
5. Anything else book-specific worth flagging before a full run (RTL/LTR,
   footnotes, running headers/footers on every page, etc.)

Output of this step: either a small per-book config (thresholds, regexes,
flags) or just a go/no-go + notes on what to double check in the output.

## Idea: font-specific Tesseract fine-tuning (learning project)

Instead of (or on top of) the geometric shear-correction workaround, fine-tune
Tesseract's `heb` LSTM model on this book's actual font, via `tesstrain`
(transfer learning from the existing model, not training from scratch).
Could plausibly fix the italic problem and the ג/נ confusion at the
recognition level rather than papering over them after the fact.

Two data sources to fine-tune on:
1. **Synthetic** - if the exact font file (incl. italic variant) can be
   identified/obtained, render large amounts of Hebrew text with it to
   generate training pairs automatically.
2. **Real scans** - hand-correct a batch of real lines from this book's
   scans as ground truth (more labor, no font file needed, captures actual
   scan/print noise).

Bigger lift than anything done so far: curating hundreds-to-thousands of
labeled lines, running `lstmtraining`/`combine_tessdata`, managing the
Tesseract training toolchain (fiddlier on Windows than Linux -- may want
WSL). Worth doing as a learning project, or if the resampling fix + shear
correction still leaves too many residual errors after a real run.

## Idea: "Introduction" / "Prologue" as a distinct pre-chapter section

Right now `create_epub_text_mode` only knows two buckets: front-matter image
pages (before `start_page`, no OCR/text) and numbered chapters (broken at
`chapter_pages`, numbered sequentially via `current_chapter_num` starting at
1). There's no way to mark an "Introduction" or "Prologue" as readable OCR'd
text that comes before "real" Chapter 1 without it either being swallowed
into the front-matter images (no text extracted) or counted as chapter 1
itself, which throws off numbering for the actual chapters that follow.

Needs a third bucket: one or more labeled pre-chapter text sections (OCR'd
same as a chapter, own title) that don't consume a slot in the real
chapter-numbering sequence. Should fit into the existing interactive prompts
in `create_epub_text_mode` (where the user already enters `start_page` and
`chapter_pages`).

## Notes from this session's calibration (book: Hebrew "843" scans)

- Confidence/height alone were too noisy to detect the bold name prefix on
  citations -- ended up keying off the literal "(" in the citation format,
  which is specific to this book.
- Centered-margin detection for headings (`is_line_centered`) worked cleanly
  and is probably generic enough to reuse as-is on other books with a
  similar "centered heading" convention.
- Italic-shear detection/correction (paragraph-level confidence flag +
  shear-angle sweep) is not book-specific -- it should carry over to any
  Hebrew book with italic passages, though thresholds may need re-checking.
- Not done yet: same treatment for Danish/English OCR (`ocr_image` currently
  only gets this special handling for `lang == 'heb'`).
