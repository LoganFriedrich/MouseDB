"""Extract code cells from the source notebook into per-section text dumps.

One-shot developer script used during the refactor. Not shipped with the tool.

Usage:
    python tools/extract_notebook_cells.py <path_to_notebook.ipynb> <output_dir>

Writes:
    <output_dir>/cells.txt          -- every code cell, one after another, with section
                                       headers from preceding markdown cells
    <output_dir>/markdown.txt       -- every markdown cell in order
    <output_dir>/toc.txt            -- one-line summary per cell (index, type, preview)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print("usage: extract_notebook_cells.py <notebook.ipynb> <out_dir>")
        sys.exit(1)
    nb_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    nb = json.loads(nb_path.read_text(encoding="utf-8"))

    cells_out = []
    md_out = []
    toc_out = []
    current_section = "(preamble)"

    for i, cell in enumerate(nb.get("cells", [])):
        source = "".join(cell.get("source", []))
        ctype = cell.get("cell_type", "")
        if ctype == "markdown":
            md_out.append(f"\n\n===== Cell {i} (markdown) =====\n{source}\n")
            first_line = source.splitlines()[0] if source else ""
            if first_line.startswith("#"):
                current_section = first_line.strip().lstrip("#").strip()
            preview = first_line[:80]
            toc_out.append(f"{i:03d}  md   {preview}")
        elif ctype == "code":
            first_line = source.splitlines()[0] if source else "(empty)"
            cells_out.append(
                f"\n\n===== Cell {i} (code) under section: {current_section} =====\n{source}\n"
            )
            preview = first_line[:80]
            toc_out.append(f"{i:03d}  code {preview}")
        else:
            toc_out.append(f"{i:03d}  {ctype}")

    (out_dir / "cells.txt").write_text("".join(cells_out), encoding="utf-8")
    (out_dir / "markdown.txt").write_text("".join(md_out), encoding="utf-8")
    (out_dir / "toc.txt").write_text("\n".join(toc_out), encoding="utf-8")
    print(f"wrote {len(cells_out)} code cells, {len(md_out)} markdown cells")
    print(f"out dir: {out_dir}")


if __name__ == "__main__":
    main()
