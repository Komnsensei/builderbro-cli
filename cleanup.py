#!/usr/bin/env python3
"""
Clean up the recovered-code/ tree:
  1. Classify each file by content (code vs prose vs tool-call payload)
  2. Dict to a clean tree: tools/, skills/, utilities/, configs/, archives/, scripts/, docs/, unknown/
  3. Write a provenance index.md
"""

import json
import os
import re
import hashlib

ROOT = "recovered-code"
OUT = "recovered-code-clean"

# Code detection heuristics
CODE_BLOCK_RE = re.compile(r"```(\w*)\n", re.MULTILINE)
CODE_LINE_RE = re.compile(r"^\s*(import |from |export |export default |def |class |async def |fn |let |const |var |package |module |require\(|console\.|<\?php|<script|function \w+|#include |namespace |struct |interface |package |\bdef\b|\bclass\b|\bexport\b|\bimport\b)\b", re.MULTILINE)
TOOL_KEYWORDS = re.compile(r"register_tool|toolName|input|output|toolCallId|dynamic_generated|SKILLS_DIR|execute_with_guardrails", re.IGNORECASE)
CODE_HINT_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".md", ".sh", ".bat", ".yml", ".yaml", ".txt", ".toml", ".cfg", ".ini", ".html", ".css", ".rs", ".go", ".java", ".kt", ".sql", ".graphql", ".env", ".wasm", ".c", ".h", ".rb", ".php", ".swift", ".vue", ".svelte"}


def md5(content):
    return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()


def classify(content):
    """Return a category: tool/skills/utility/config/archive/docs/prose/unknown."""
    if not content.strip():
        return "unknown"
    # Explicit code block markers
    if CODE_BLOCK_RE.search(content):
        m = CODE_BLOCK_RE.search(content)
        lang = m.group(1).lower()
        if lang in {"python", "javascript", "typescript", "bash", "sh", "json", "md", "py", "js", "ts", "go", "rust", "c", "cpp", "java"}:
            return "tool" if TOOL_KEYWORDS.search(content) else "code"
        return "code"
    # Code markers
    if CODE_LINE_RE.search(content):
        return "tool" if TOOL_KEYWORDS.search(content) else "code"
    # Tool/call payload
    if TOOL_KEYWORDS.search(content):
        return "tool"
    # Short lines that look like code with braces/parens
    code_lines = [l for l in content.splitlines() if re.search(r"[{};:()\"'=<>\[\]]$", l.strip())]
    if len(code_lines) >= 3:
        return "code"
    # Check for JSON-like structure with code markers spread across lines
    if re.search(r"(import |from |export |def |class |async def |function |const |let |var |require\(|console\.)", content):
        return "tool" if TOOL_KEYWORDS.search(content) else "code"
    return "prose"


def is_code_file(relpath):
    ext = os.path.splitext(relpath)[1].lower()
    return ext in CODE_HINT_EXTS


def clean_filename(s, maxlen=60):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(s))
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:maxlen] or "item"


def write_with_dirs(root, rel, content):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content if content.endswith("\n") else content + "\n")


def gen_provenance(index, path, rel_rel, orig_path, size, category):
    rel = os.path.relpath(path, ROOT)
    idx = {
        "rel_path": rel,
        "original_path": orig_path,
        "size_bytes": size,
        "category": category,
        "hash": md5(open(path, "rb").read().decode("utf-8", errors="replace")),
    }
    index["files"].append(idx)


def main():
    if not os.path.isdir(ROOT):
        print(f"missing {ROOT}")
        return

    all_files = []
    for dirpath, _, filenames in os.walk(ROOT):
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, ROOT)
            size = os.path.getsize(full)
            try:
                content = open(full, "r", encoding="utf-8", errors="replace").read()
            except Exception:
                content = ""
            all_files.append((full, rel, size, content))

    print(f"Scanning {len(all_files)} files...")

    dedup_map = {}
    for full, rel, size, content in all_files:
        cat = classify(content)
        h = md5(content)
        key = (cat, h)
        if key in dedup_map:
            dedup_map[key].append((full, rel, size, cat))
        else:
            dedup_map[key] = [(full, rel, size, cat)]

    print(f"Unique content groups: {len(dedup_map)}")

    os.makedirs(OUT, exist_ok=True)

    index = {"generated": "2026-10-04", "total_files": 0, "files": []}

    for (cat, h), group in sorted(dedup_map.items()):
        rep = group[0]
        rep_full, rep_rel, rep_size, rep_cat = rep
        content = rep[3]

        cat_map = {
            "tool": "tools",
            "code": "tools",
            "skills": "skills",
            "utility": "utilities",
            "config": "configs",
            "docs": "docs",
            "archive": "archives",
            "script": "scripts",
            "prose": "prose",
            "unknown": "unknown",
        }
        subdir = cat_map.get(cat, "unknown")

        base = os.path.splitext(os.path.basename(rep_rel))[0]
        if rep_cat in ("tool", "code"):
            m = re.search(r'"(\w+)"', content[:200])
            if m:
                base = f"{m.group(1)}_{h[:8]}"

        clean_rel = os.path.join(subdir, base + ".json")
        clean_path = os.path.join(OUT, clean_rel)
        if os.path.exists(clean_path):
            clean_rel = os.path.join(subdir, f"{base}_{h[:8]}.json")
            clean_path = os.path.join(OUT, clean_rel)

        write_with_dirs(OUT, clean_rel, content)
        gen_provenance(index, rep_full, rep_rel, rep_full, rep_size, rep_cat)
        index["total_files"] += 1

    with open(os.path.join(OUT, "index.md"), "w", encoding="utf-8") as f:
        f.write("# Recovered Code Index\n\n")
        f.write(f"Generated: 2026-10-04\n\n")
        f.write(f"Total files: {index['total_files']}\n\n")
        f.write("## By Category\n\n")
        cat_counts = {}
        for idx in index["files"]:
            c = idx["category"]
            cat_counts[c] = cat_counts.get(c, 0) + 1
        for c, n in sorted(cat_counts.items()):
            f.write(f"- **{c}**: {n}\n")
        f.write("\n## File Manifest\n\n")
        f.write("| File | Original | Size | Category |\n|---|---|---|---|\n")
        for idx in sorted(index["files"], key=lambda x: x["rel_path"]):
            f.write(f"| {idx['rel_path']} | {idx['original_path']} | {idx['size_bytes']} | {idx['category']} |\n")
        f.write("\n")

    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    print(f"Done. Wrote {index['total_files']} files to {OUT}/")
    print(f"Index: {OUT}/index.md and {OUT}/index.json")


if __name__ == "__main__":
    main()
