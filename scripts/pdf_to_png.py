"""One-off utility: convert Master Plan PDFs to high-resolution PNGs.

The Master Plan maps from São José (LC 173/2024) are distributed as
vector PDFs. For georeferencing in QGIS we need raster images. This
script renders each PDF to a single PNG at a configurable DPI using
PyMuPDF (fitz), which has no external dependencies on Windows.

Usage:
    python scripts/pdf_to_png.py --region sao_jose_sc
    python scripts/pdf_to_png.py --region sao_jose_sc --dpi 400 --force

Notes:
    - Master Plan annexes are single-page documents, so only page 1 is
      rendered. If a multi-page PDF is encountered, a warning is logged
      and only the first page is used.
    - Default DPI of 300 balances detail vs file size (~20-40 MB each).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Render DPI. Higher = sharper images but bigger files.
# 300 DPI is the sweet spot for georeferencing: enough detail to identify
# fine features without making PNGs unmanageably large.
DEFAULT_DPI = 300


def find_project_root() -> Path:
    """Return the repository root (parent of the scripts/ directory)."""
    return Path(__file__).resolve().parents[1]


def list_pdfs(pdf_dir: Path) -> list[Path]:
    """Return all PDFs in ``pdf_dir`` sorted alphabetically."""
    return sorted(pdf_dir.glob("*.pdf"))


def convert_pdf(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    force: bool,
) -> Path | None:
    """Render the first page of ``pdf_path`` as PNG.

    Returns:
        The output path, or None if the file was skipped (already exists).
    """
    out_path = out_dir / f"{pdf_path.stem}.png"

    if out_path.exists() and not force:
        logger.info("Skipping (already exists): %s", out_path.name)
        return None

    logger.info("Rendering %s at %d DPI...", pdf_path.name, dpi)
    doc = fitz.open(pdf_path)
    try:
        if doc.page_count == 0:
            logger.error("PDF has no pages: %s", pdf_path)
            return None
        if doc.page_count > 1:
            logger.warning(
                "PDF %s has %d pages; only rendering the first.",
                pdf_path.name,
                doc.page_count,
            )

        page = doc.load_page(0)
        # Render at the requested DPI. PyMuPDF's default is 72 DPI;
        # scaling factor = dpi / 72.
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)

        out_dir.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(out_path))

        size_mb = out_path.stat().st_size / 1024 / 1024
        logger.info(
            "  -> %s (%dx%d px, %.1f MB)",
            out_path.name,
            pixmap.width,
            pixmap.height,
            size_mb,
        )
        return out_path
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--region",
        required=True,
        help='Region slug (e.g. "sao_jose_sc")',
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"Render DPI (default: {DEFAULT_DPI})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-render PNGs even if they already exist.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    root = find_project_root()
    pdf_dir = root / "data" / "raw" / args.region / "master_plan"
    out_dir = root / "data" / "raw" / args.region / "master_plan" / "rendered"

    if not pdf_dir.exists():
        logger.error("PDF directory not found: %s", pdf_dir)
        return 1

    pdfs = list_pdfs(pdf_dir)
    if not pdfs:
        logger.error("No PDFs found in %s", pdf_dir)
        return 1

    logger.info("Found %d PDFs in %s", len(pdfs), pdf_dir)
    converted = 0
    for pdf in pdfs:
        result = convert_pdf(pdf, out_dir, args.dpi, args.force)
        if result is not None:
            converted += 1

    logger.info(
        "Done. %d PDFs converted (out of %d total) to %s",
        converted,
        len(pdfs),
        out_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())