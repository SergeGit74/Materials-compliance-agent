"""Retrieval over the rulebooks in rules/ (README 8.4): chunk by clause, keep file and section as metadata.

Ranking is lexical (BM25, no API). If an embedder is given (Voyage), the BM25 shortlist is re-ranked by embedding
similarity; only the shortlist is embedded and the vectors are cached, so the cost per item is small.
Only text from rules/ and the normalized product fields go to the embedding service (README 1.4).

Citations are `file#section`. A section id is the clause number found in the text ("4.5.1", "D.5"). When the same
number repeats in a file (chapters restart numbering) the id gets the page: "6.1.1@p856".
Files whose text layer is unreadable are skipped and listed, never searched.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config

CACHE = config.CACHE_DIR / "rules_chunks.json"
VEC_CACHE = config.CACHE_DIR / "rules_vectors.json"
MAX_CHARS = 1800
HEAD = re.compile(r"^\s*((?:[A-K]\.)?\d{1,3}(?:\.\d{1,3}){0,5})\.?\s+([A-Z][^\n]{2,90})$")
STOP = set("the a an and or of to in for on with by is are be shall as at from that this it its not no any all".split())
# Text-layer decoders for files known to need one (see memory note rules-pdf-text-layers).
SHIFTED = {"dubai-municipality_seismic-design-code_v2026-04.pdf"}


@dataclass
class Chunk:
    file: str
    section: str
    page: int
    text: str

    @property
    def ref(self) -> str:
        return f"{self.file}#{self.section}"


def confirmation_status(rules_dir: Path = config.RULES_DIR) -> dict[str, str] | None:
    """file -> 'confirmed' or 'pending' from rules/INDEX.md, or None when there is no INDEX.md (then every file counts, as in the tests).
    A row whose 'Confirmed by' cell is empty or starts with PENDING is not citable until a person confirms it."""
    idx = Path(rules_dir) / "INDEX.md"
    if not idx.exists():
        return None
    out = {}
    for line in idx.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and cells[0].lower().endswith((".pdf", ".md", ".txt")):
            out[cells[0]] = "pending" if (not cells[6] or cells[6].upper().startswith("PENDING")) else "confirmed"
    return out


def citable_files(rules_dir: Path = config.RULES_DIR) -> list[str]:
    """Files in rules/ that may be cited: not the folder's README or INDEX, and confirmed in INDEX.md (when it exists)."""
    d = Path(rules_dir)
    if not d.exists():
        return []
    skip = {"readme.md", "index.md"}
    names = sorted(p.name for p in d.iterdir() if p.is_file() and p.name.lower() not in skip)
    gate = confirmation_status(d)
    return names if gate is None else [n for n in names if gate.get(n) == "confirmed"]


def decode_shifted(text: str) -> str:
    """The seismic PDF stores letters 29 code points too low (digits and spaces are lost)."""
    return "".join(chr(ord(c) + 29) if 33 <= ord(c) <= 98 else c for c in text)


def pdf_pages(path: Path) -> list[str] | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    r = subprocess.run([exe, str(path), "-"], capture_output=True, timeout=600)
    return r.stdout.decode("utf-8", errors="replace").split("\f")


def readable(pages: list[str]) -> bool:
    from .docsearch import text_quality
    sample = " ".join(pages[: min(len(pages), 60)])[:20000]
    return text_quality(sample) == "english text ok"


def chunk_pages(file: str, pages: list[str]) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: dict[str, int] = {}
    used: set[str] = set()
    cur_id, cur_slug, cur_page, buf = None, "", 1, []

    def flush():
        nonlocal cur_id, cur_slug, buf
        text = " ".join(" ".join(buf).split())
        if cur_id and len(text) > 40:
            base = cur_id + (f"-{cur_slug}" if cur_slug else "")
            sid = base if base not in used else f"{base}@p{cur_page}"
            n = 2
            while sid in used:
                sid = f"{cur_id}@p{cur_page}-{n}"
                n += 1
            used.add(sid)
            seen[cur_id] = seen.get(cur_id, 0) + 1
            chunks.append(Chunk(file, sid, cur_page, text[:MAX_CHARS * 2]))
        buf = []

    for pno, page in enumerate(pages, 1):
        has_head = False
        for line in page.splitlines():
            m = HEAD.match(line)
            if m:
                has_head = True
                flush()
                cur_id, cur_page = m.group(1), pno
                cur_slug = re.sub(r"[^A-Za-z0-9]+", "-", m.group(2)).strip("-")[:40].strip("-")
                buf = [m.group(2)]
            elif cur_id:
                buf.append(line)
                if sum(len(x) for x in buf) > MAX_CHARS:
                    flush()
                    cur_page = pno
        if not has_head and not cur_id:
            text = " ".join(page.split())
            if len(text) > 80:
                chunks.append(Chunk(file, f"p{pno}", pno, text[:MAX_CHARS * 2]))
    flush()
    return chunks


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


CLAUSE_ID = re.compile(r"^(?:[A-K]\.)?\d{1,3}(?:\.\d{1,3})+(?:-|$|@)|^[A-K]\.\d{1,3}(?:-|$|@)")


def is_clause_heading(section: str) -> bool:
    """A real clause heading has a dotted number ("4.5.1-...", "K.3.6-...") or a letter part ("D.5-..."). Appendix table rows ("40-KA",
    "1-Mounting") and bare page chunks ("p2") are noise for the model that picks clauses from the heading list."""
    return bool(CLAUSE_ID.match(section))


def tokens(text: str) -> list[str]:
    """Words and numbers; hyphenated codes split ("60947-2" gives "60947" and "2"); a trailing plural s is dropped."""
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def file_signature(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}-{int(st.st_mtime)}"


class RuleIndex:
    def __init__(self, rules_dir: Path = config.RULES_DIR, cache: Path = CACHE):
        self.rules_dir, self.cache = Path(rules_dir), Path(cache)
        self.chunks: list[Chunk] = []
        self.skipped: dict[str, str] = {}
        self._df: dict[str, int] = {}
        self._tf: list[dict[str, int]] = []
        self._len: list[int] = []
        self._parent: list[int] = []
        self.embed_error = ""
        self.report: list[dict] = []

    # --- build / load -------------------------------------------------------------------------------------
    def files(self) -> list[Path]:
        skip = {"readme.md", "index.md"}
        return sorted(p for p in self.rules_dir.iterdir() if p.is_file() and p.name.lower() not in skip) if self.rules_dir.exists() else []

    def signature(self) -> tuple:
        """Changes when a file in rules/ or INDEX.md is added, removed or edited, so a running app knows to reload."""
        d = self.rules_dir
        parts = [(p.name, p.stat().st_size, int(p.stat().st_mtime)) for p in self.files()]
        idx = d / "INDEX.md"
        return tuple(parts) + ((("INDEX.md", idx.stat().st_size, int(idx.stat().st_mtime)),) if idx.exists() else ())

    def load(self, force: bool = False) -> "RuleIndex":
        cached = json.loads(self.cache.read_text(encoding="utf-8")) if self.cache.exists() and not force else {}
        out = {}
        gate = confirmation_status(self.rules_dir)
        self.report = []
        for p in self.files():
            if gate is not None and gate.get(p.name) != "confirmed":
                why = "not in INDEX.md" if p.name not in gate else "waiting for a person's confirmation in INDEX.md"
                self.skipped[p.name] = why
                self.report.append({"file": p.name, "status": why, "clauses": 0})
                continue
            sig = file_signature(p)
            entry = cached.get(p.name)
            if entry and entry["sig"] == sig and entry.get("v") == 2:
                out[p.name] = entry
                continue
            out[p.name] = {"sig": sig, "v": 2, **self._read(p)}
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps(out), encoding="utf-8")
        for name, e in out.items():
            if e.get("skipped"):
                self.skipped[name] = e["skipped"]
                self.report.append({"file": name, "status": "skipped: " + e["skipped"], "clauses": 0})
            else:
                self.chunks += [Chunk(name, *c) for c in e["chunks"]]
                self.report.append({"file": name, "status": "indexed", "clauses": len(e["chunks"])})
        for name in (gate or {}):
            if not (self.rules_dir / name).exists():
                self.report.append({"file": name, "status": "missing: listed in INDEX.md but not in rules/ (run python -m src.fetch_rules)", "clauses": 0})
        self._build_stats()
        return self

    def _read(self, p: Path) -> dict:
        if p.suffix.lower() == ".pdf":
            pages = pdf_pages(p)
            if pages is None:
                return {"skipped": "pdftotext not available"}
            if p.name in SHIFTED:
                pages = [decode_shifted(x) for x in pages]
                chunks = chunk_pages(p.name, pages)
                return {"chunks": [[c.section, c.page, c.text] for c in chunks]} if chunks else {"skipped": "no text after decoding"}
            if not readable(pages):
                return {"skipped": "text layer is not readable English"}
        else:
            pages = [p.read_text(encoding="utf-8", errors="replace")]
        chunks = chunk_pages(p.name, pages)
        return {"chunks": [[c.section, c.page, c.text] for c in chunks]}

    def _build_stats(self) -> None:
        self._tf, self._len, self._df = [], [], {}
        for c in self.chunks:
            toks = tokens(c.section + " " + c.section + " " + c.text)  # heading words count double
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t in tf:
                self._df[t] = self._df.get(t, 0) + 1
            self._tf.append(tf)
            self._len.append(len(toks))

    def toc(self, max_chars: int = 330_000, clean: bool = True) -> tuple[str, list[Chunk]]:
        """Numbered list of clause headings, grouped by file, for the model to choose from (about 12 tokens per line).
        Returns the text and the chunks in the same order, so a number maps back to its clause. Capped at max_chars."""
        lines, chunks, used = [], [], 0
        # a file whose headings are mostly not dotted clause numbers (SIRA, seismic, wind) keeps all of them except bare page chunks
        total, dotted = {}, {}
        for c in self.chunks:
            total[c.file] = total.get(c.file, 0) + 1
            dotted[c.file] = dotted.get(c.file, 0) + is_clause_heading(c.section)
        loose = {f for f in total if dotted[f] / total[f] < 0.3}
        for f in sorted({c.file for c in self.chunks}):
            head = f"## {f}"
            lines.append(head)
            used += len(head) + 1
            for c in self.chunks:
                if c.file != f or (clean and not (is_clause_heading(c.section) or (f in loose and not re.fullmatch(r"p\d+", c.section)))):
                    continue
                line = f"{len(chunks)} {c.section[:60]}"
                if used + len(line) + 1 > max_chars:
                    break
                lines.append(line)
                chunks.append(c)
                used += len(line) + 1
        return "\n".join(lines), chunks

    # --- search ---------------------------------------------------------------------------------------------
    def bm25(self, query: str, k: int = 40, files: set[str] | None = None) -> list[tuple[float, Chunk]]:
        q = set(tokens(query))
        n = len(self.chunks)
        avg = (sum(self._len) / n) if n else 1
        scored = []
        for i, c in enumerate(self.chunks):
            if files and c.file not in files:
                continue
            tf, s = self._tf[i], 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                    s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * self._len[i] / avg))
            if s > 0:
                scored.append((s, c))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def search(self, query: str, k: int = 12, embedder=None, files: set[str] | None = None) -> list[Chunk]:
        short = self.bm25(query, 30 if embedder else k, files)
        if not embedder or not short:
            return [c for _, c in short[:k]]
        try:
            vecs = embed_cached(embedder, [query] + [c.text[:700] for _, c in short])
        except Exception as e:  # rate limit, network or key problems: fall back to keyword ranking, never fail the item
            self.embed_error = type(e).__name__
            return [c for _, c in short[:k]]
        qv, rest = vecs[0], vecs[1:]
        ranked = sorted(zip(rest, short), key=lambda x: -cosine(qv, x[0]) - 0.002 * x[1][0])
        return [c for _, (_, c) in ranked[:k]]


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def embed_cached(embedder, texts: list[str], cache: Path | None = None) -> list[list[float]]:
    """Embed texts, reusing vectors already paid for. The cache key is the model name plus a hash of the text."""
    cache = Path(cache or VEC_CACHE)
    store = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    keys = [f"{embedder.model}:{hashlib.sha256(t.encode('utf-8')).hexdigest()[:20]}" for t in texts]
    todo = [i for i, k in enumerate(keys) if k not in store]
    if todo:
        for i, v in zip(todo, embedder.embed([texts[i] for i in todo])):
            store[keys[i]] = v
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps(store), encoding="utf-8")
    return [store[k] for k in keys]


class VoyageEmbedder:
    """Voyage embeddings. The key comes from the environment; it is never printed."""

    def __init__(self, model: str, client=None):
        import os
        self.model = model
        if client is None:
            import voyageai
            from dotenv import load_dotenv
            load_dotenv(config.ROOT / ".env")
            client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
        self.client = client
        self.tokens_used = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for i in range(0, len(texts), 64):
            r = self.client.embed(texts[i:i + 64], model=self.model, input_type="document")
            self.tokens_used += getattr(r, "total_tokens", 0)
            out += r.embeddings
        return out


def main(argv: list[str] | None = None) -> int:
    """python -m src.rules_index build | search "query" [--k N] [--embed]. build and search make no API call unless --embed."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m src.rules_index")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="chunk the rulebooks in rules/ and cache the chunks (no API call)")
    sp = sub.add_parser("search", help="show the clauses a query retrieves")
    sp.add_argument("query")
    sp.add_argument("--k", type=int, default=8)
    sp.add_argument("--embed", action="store_true", help="re-rank with Voyage (spends a few Voyage tokens)")
    a = ap.parse_args(argv)
    idx = RuleIndex().load(force=a.cmd == "build")
    if a.cmd == "build":
        per: dict[str, int] = {}
        for c in idx.chunks:
            per[c.file] = per.get(c.file, 0) + 1
        for f, n in sorted(per.items()):
            print(f"{n:6d} chunks  {f}")
        for f, why in idx.skipped.items():
            print(f"  skipped  {f}: {why}")
        return 0
    emb = None
    if a.embed:
        import os
        from dotenv import load_dotenv
        load_dotenv(config.ROOT / ".env")
        emb = VoyageEmbedder(os.environ["VOYAGE_EMBED_MODEL"])
    for c in idx.search(a.query, a.k, embedder=emb):
        print(f"{c.ref}  (p.{c.page})  {c.text[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
