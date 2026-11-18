#!/usr/bin/env python3
"""Danish EPUB -> bilingual EPUB: each Danish sentence is followed, on the next
line, by a plain English translation from Gemini.

    GEMINI_API_KEY=... python3 bilingual_epub.py universitet.epub universitet_bilingual.epub

Features
  * Inline markup (italics, links, spans ...) inside a paragraph is preserved:
    sentences are cut out of the original runs instead of flattening to text.
  * Chapters come from the EPUB's spine (no hardcoded file names).
  * Translations are cached on disk (<output>.cache/translations.json), so a
    crash or a re-run never pays twice for the same sentence.
  * Every API call's tokens are priced; the run STOPS and prints a loud alert
    once the cumulative cost passes --budget (default USD 1.00).
  * After building, the Danish text of every chapter is compared with the
    original to prove nothing was lost or altered.

Needs: python3, lxml.  The API key is read from the environment only.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile

from lxml import etree

XHTML = "http://www.w3.org/1999/xhtml"
NS = {"x": XHTML}
MODEL = "gemini-3.1-flash-lite"
# USD per 1M tokens (lib/ai_model_pricing.dart, geminiModelFallback2).
RATE_IN, RATE_OUT = 0.25, 1.50
BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p")
TR_STYLE = "color:#5a5a5a;font-size:0.85em;"

SYSTEM_PROMPT = (
    "You translate Danish into plain, basic English for a language learner who "
    "reads the Danish and glances at your English only to look up words they "
    "do not know. Stay close to the Danish wording and word order wherever "
    "natural English allows, so words are easy to match. Do not paraphrase, "
    "summarise, explain or add anything. Keep names, numbers and units. "
    "The input is a JSON array of Danish sentences; return a JSON array of "
    "English strings with EXACTLY the same number of items, same order."
)

# Danish abbreviations whose final period does not end a sentence.
ABBREVIATIONS = {
    "dvs", "f.eks", "feks", "osv", "mv", "mfl", "m.fl", "ca", "kl", "nr", "st",
    "evt", "jf", "fx", "vha", "pga", "bl.a", "m.m", "mm", "tlf", "ifm", "inkl",
    "excl", "ex", "o.l", "o.lign", "dr", "hr", "fru", "prof", "mht", "iflg",
}
SPLIT_RE = re.compile(r"[.!?][\"”»’)]*\s+(?=[A-ZÆØÅ\"“«„(])")


# --------------------------------------------------------------------------
# Sentence splitting that keeps inline markup
# --------------------------------------------------------------------------
class Unsupported(Exception):
    """Block contains something we cannot safely rebuild (e.g. <img>)."""


def collect_chars(el):
    """Flatten `el` into [(char, chain)] where chain is the tuple of inline
    ancestor elements (below `el`) that wrap that character."""
    out = []

    def add(text, chain):
        for ch in text:
            out.append((ch, chain))

    def walk(node, chain):
        add(node.text or "", chain)
        for child in node:
            if not isinstance(child.tag, str):  # comment / PI
                add(child.tail or "", chain)
                continue
            name = etree.QName(child).localname
            if name == "br":
                add(" ", chain)
            elif len(child) == 0 and not (child.text or "").strip():
                raise Unsupported(name)  # img, empty anchor, ...
            else:
                walk(child, chain + (child,))
            add(child.tail or "", chain)

    walk(el, ())
    # collapse whitespace (keep the chain of the first space), then strip
    collapsed = []
    for ch, chain in out:
        if ch.isspace():
            if collapsed and collapsed[-1][0] == " ":
                continue
            ch = " "
        collapsed.append((ch, chain))
    while collapsed and collapsed[0][0] == " ":
        collapsed.pop(0)
    while collapsed and collapsed[-1][0] == " ":
        collapsed.pop()
    return collapsed


def sentence_spans(plain):
    spans, start = [], 0
    for m in SPLIT_RE.finditer(plain):
        head = plain[start:m.start() + 1]
        last_word = re.split(r"\s", head.rstrip(".!?"))[-1].lower()
        if m.group()[0] == "." and last_word in ABBREVIATIONS:
            continue
        cut = m.start() + len(m.group().rstrip())
        spans.append((start, cut))
        start = m.end()
    if start < len(plain):
        spans.append((start, len(plain)))
    return spans


def append_text(parent, text):
    if len(parent):
        parent[-1].tail = (parent[-1].tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def emit_sentence(parent, chars):
    """Append chars[a:b] to `parent`, re-wrapping runs in clones of their
    original inline elements."""
    i = 0
    while i < len(chars):
        chain = chars[i][1]
        j = i
        while j < len(chars) and chars[j][1] == chain:
            j += 1
        text = "".join(c for c, _ in chars[i:j])
        if not chain:
            append_text(parent, text)
        else:
            top = inner = None
            for orig in chain:
                clone = etree.Element(orig.tag, dict(orig.attrib))
                if top is None:
                    top = inner = clone
                else:
                    inner.append(clone)
                    inner = clone
            inner.text = text
            parent.append(top)
        i = j


def split_block(el):
    """Return [(plain_sentence, chars_slice)] for a block element."""
    chars = collect_chars(el)
    plain = "".join(c for c, _ in chars)
    return [(plain[a:b], chars[a:b]) for a, b in sentence_spans(plain)]


# --------------------------------------------------------------------------
# Gemini client with token/cost accounting
# --------------------------------------------------------------------------
class BudgetExceeded(Exception):
    pass


class Ledger:
    def __init__(self, path, budget):
        self.path, self.budget = path, budget
        self.d = {"in": 0, "out": 0, "calls": 0, "failed_calls": 0,
                  "usd": 0.0, "alerted": False}
        if os.path.exists(path):
            self.d.update(json.load(open(path)))

    def add(self, tok_in, tok_out):
        self.d["in"] += tok_in
        self.d["out"] += tok_out
        self.d["calls"] += 1
        self.d["usd"] = (self.d["in"] * RATE_IN + self.d["out"] * RATE_OUT) / 1e6
        json.dump(self.d, open(self.path, "w"), indent=2)
        if self.d["usd"] > self.budget and not self.d["alerted"]:
            self.d["alerted"] = True
            json.dump(self.d, open(self.path, "w"), indent=2)
            print(f"\n{'!' * 70}\n!! COST ALERT: USD {self.d['usd']:.4f} used, "
                  f"over the USD {self.budget:.2f} budget. Stopping.\n{'!' * 70}",
                  flush=True)

    def check(self):
        if self.d["usd"] > self.budget:
            raise BudgetExceeded()


def call_gemini(api_key, sentences, ledger):
    ledger.check()
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user",
                      "parts": [{"text": json.dumps(sentences, ensure_ascii=False)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.load(r)
            u = resp.get("usageMetadata", {})
            # thinking tokens are billed at the output rate
            ledger.add(u.get("promptTokenCount", 0),
                       u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0))
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"Gemini error {e.code}: {e.read()[:300]!r}")
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError,
                json.JSONDecodeError) as e:
            last = type(e).__name__
        ledger.d["failed_calls"] += 1
        time.sleep(2 ** attempt)
        ledger.check()
    raise RuntimeError(f"Gemini failed after retries ({last})")


def translate_batch(api_key, sentences, ledger):
    """Translate; if the item count comes back wrong, bisect until it matches."""
    result = call_gemini(api_key, sentences, ledger)
    if isinstance(result, list) and len(result) == len(sentences) \
            and all(isinstance(s, str) and s.strip() for s in result):
        return result
    if len(sentences) == 1:
        raise RuntimeError(f"Bad translation for: {sentences[0][:80]!r}")
    mid = len(sentences) // 2
    return (translate_batch(api_key, sentences[:mid], ledger)
            + translate_batch(api_key, sentences[mid:], ledger))


def key_of(sentence):
    return hashlib.sha1(sentence.encode()).hexdigest()


# --------------------------------------------------------------------------
# EPUB handling
# --------------------------------------------------------------------------
def spine_chapters(z):
    container = etree.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.xpath("//*[local-name()='rootfile']/@full-path")[0]
    opf = etree.fromstring(z.read(opf_path))
    base = os.path.dirname(opf_path)
    items = {i.get("id"): i for i in opf.xpath("//*[local-name()='manifest']/*")}
    out = []
    for ref in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']"):
        it = items[ref.get("idref")]
        if "nav" in (it.get("properties") or "").split():
            continue
        if it.get("media-type") != "application/xhtml+xml":
            continue
        out.append(os.path.normpath(os.path.join(base, it.get("href"))).replace(os.sep, "/"))
    return out


def parse(data):
    return etree.ElementTree(etree.fromstring(data, etree.XMLParser(recover=True)))


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--budget", type=float, default=1.00, help="USD hard stop")
    ap.add_argument("--max-sentences", type=int, default=0,
                    help="translate only this many NEW sentences (testing)")
    ap.add_argument("--batch", type=int, default=40)
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("Set GEMINI_API_KEY in the environment.")
    if not os.path.exists(args.input):
        sys.exit(f"Cannot find {args.input}")

    cache_dir = args.output + ".cache"
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, "translations.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    ledger = Ledger(os.path.join(cache_dir, "cost.json"), args.budget)

    z = zipfile.ZipFile(args.input)
    chapters = spine_chapters(z)
    print(f"Chapters: {chapters}")

    # Phase 1: parse + split every chapter
    docs, todo = {}, []
    for name in chapters:
        tree = parse(z.read(name))
        blocks = []
        for el in tree.getroot().xpath(
                "//x:body//*[" + " or ".join(f"self::x:{t}" for t in BLOCK_TAGS) + "]",
                namespaces=NS):
            try:
                sents = split_block(el)
            except Unsupported as e:
                print(f"  skip block with <{e}> in {name}")
                continue
            if sents:
                blocks.append((el, sents))
                todo += [s for s, _ in sents if key_of(s) not in cache]
        docs[name] = (tree, blocks)
    todo = list(dict.fromkeys(todo))
    if args.max_sentences:
        todo = todo[:args.max_sentences]
    total_sent = sum(len(s) for _, b in docs.values() for _, s in b)
    print(f"{total_sent} sentences, {len(todo)} to translate "
          f"(cached: {len(cache)}), model {MODEL}")

    # Phase 2: translate in batches, saving cache + cost after every batch
    stopped = False
    for i in range(0, len(todo), args.batch):
        batch = todo[i:i + args.batch]
        try:
            for s, en in zip(batch, translate_batch(api_key, batch, ledger)):
                cache[key_of(s)] = en.strip()
        except BudgetExceeded:
            stopped = True
            break
        json.dump(cache, open(cache_path, "w"), ensure_ascii=False)
        print(f"  {min(i + args.batch, len(todo))}/{len(todo)}  "
              f"cost so far ${ledger.d['usd']:.4f}", flush=True)
        if ledger.d["alerted"]:
            stopped = True
            break
    json.dump(cache, open(cache_path, "w"), ensure_ascii=False)

    missing = [s for _, b in docs.values() for _, ss in b for s, _ in ss
               if key_of(s) not in cache]
    if missing:
        print(f"\nNot building the EPUB: {len(missing)} sentences still untranslated"
              + (" (stopped on budget)" if stopped else " (--max-sentences test run)")
              + f". Re-run to continue; cached work is kept.")
        report(ledger)
        sys.exit(2 if stopped else 0)

    # Phase 3: rebuild chapters
    new_files = {}
    for name, (tree, blocks) in docs.items():
        for el, sents in blocks:
            el.text = None
            for child in list(el):
                el.remove(child)
            for idx, (plain, chars) in enumerate(sents):
                if idx:
                    etree.SubElement(el, f"{{{XHTML}}}br")
                emit_sentence(el, chars)
                etree.SubElement(el, f"{{{XHTML}}}br")
                em = etree.SubElement(el, f"{{{XHTML}}}em",
                                      {"class": "tr", "style": TR_STYLE})
                em.text = cache[key_of(plain)]
        new_files[name] = etree.tostring(tree, encoding="utf-8", xml_declaration=True)

    # Phase 4: write EPUB preserving original member order (mimetype first, stored)
    with zipfile.ZipFile(args.output, "w") as out:
        for info in z.infolist():
            data = new_files.get(info.filename, z.read(info.filename))
            ctype = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            out.writestr(info.filename, data, compress_type=ctype)
    print(f"Wrote {args.output}")

    # Phase 5: verify
    ok = verify(args.input, args.output, chapters, docs)
    report(ledger)
    sys.exit(0 if ok else 1)


def danish_text(root):
    """Text of the document with translation <em class=tr> removed."""
    r = copy.deepcopy(root)
    for em in r.xpath("//x:em[@class='tr']", namespaces=NS):
        em.getparent().remove(em)
    for br in r.xpath("//x:br", namespaces=NS):  # a line break reads as a space
        br.tail = " " + (br.tail or "")
    return [norm("".join(b.itertext())) for b in r.xpath(
        "//x:body//*[" + " or ".join(f"self::x:{t}" for t in BLOCK_TAGS) + "]",
        namespaces=NS)]


def verify(src, dst, chapters, docs):
    print("\nVerification")
    ok = True
    zi, zo = zipfile.ZipFile(src), zipfile.ZipFile(dst)
    if zo.testzip() is not None:
        print("  FAIL: corrupt zip member"); ok = False
    first = zo.infolist()[0]
    if first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
        print("  FAIL: mimetype must be first and uncompressed"); ok = False
    if [i.filename for i in zi.infolist()] != [i.filename for i in zo.infolist()]:
        print("  FAIL: file list differs"); ok = False
    for name in chapters:
        before = danish_text(parse(zi.read(name)).getroot())
        root = etree.fromstring(zo.read(name))  # strict parse: must be well-formed
        after = danish_text(root)
        n_tr = len(root.xpath("//x:em[@class='tr']", namespaces=NS))
        n_exp = sum(len(s) for _, s in docs[name][1])
        same = before == after
        print(f"  {name}: Danish text identical={same}, "
              f"translations={n_tr}/{n_exp}")
        ok &= same and n_tr == n_exp
    print("  RESULT:", "OK" if ok else "PROBLEMS FOUND")
    return ok


def report(ledger):
    d = ledger.d
    print(f"\nCost report ({MODEL}): {d['calls']} calls "
          f"({d['failed_calls']} retried), {d['in']:,} input + {d['out']:,} output tokens "
          f"= USD {d['usd']:.4f}  (budget USD {ledger.budget:.2f})")


if __name__ == "__main__":
    main()
