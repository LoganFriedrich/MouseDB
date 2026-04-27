"""
Figure export with provenance metadata.

Saves figures as PNG (always) with optional JSON sidecar containing full
provenance (what data, what script, what parameters, when generated).
"""

import json
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt


def save_figure(fig, path, dpi=200, metadata=None, sidecar=True,
                formats=None, facecolor="white", register=False,
                registry_kwargs=None):
    """Save a matplotlib figure with provenance tracking.

    Parameters
    ----------
    fig : matplotlib Figure
    path : str or Path
        Output file path (primary format, usually .png).
    dpi : int
        Resolution. 200 for presentation, 300 for publication.
    metadata : dict, optional
        Provenance metadata to embed. Keys typically include:
        title, script, data_sources, generated_at, mode, dpi.
    sidecar : bool
        If True, writes a .json sidecar file alongside the image with
        full provenance metadata.
    formats : list of str, optional
        Additional export formats (e.g., ["svg", "pdf"]).
        The primary path format is always saved.
    facecolor : str
        Background color. Default "white" (lab standard).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Save primary format
    fig.savefig(
        str(path),
        dpi=dpi,
        bbox_inches="tight",
        facecolor=facecolor,
    )

    # Save additional formats
    if formats:
        for fmt in formats:
            alt_path = path.with_suffix(f".{fmt}")
            fig.savefig(
                str(alt_path),
                dpi=dpi,
                bbox_inches="tight",
                facecolor=facecolor if fmt == "png" else "none",
            )

    # Write JSON sidecar with provenance
    if sidecar and metadata:
        sidecar_path = path.with_suffix(".json")
        sidecar_data = {
            "figure_file": path.name,
            "generated_at": metadata.get(
                "generated_at", datetime.now().isoformat()
            ),
            "script": metadata.get("script", "unknown"),
            "data_sources": metadata.get("data_sources", []),
            "title": metadata.get("title", ""),
            "dpi": dpi,
            "mode": metadata.get("mode", "presentation"),
            "formats_saved": [path.suffix.lstrip(".")] + (formats or []),
        }
        with open(sidecar_path, "w") as f:
            json.dump(sidecar_data, f, indent=2, default=str)

    # Register in FigureRegistry if requested
    if register:
        try:
            from mousedb.registry import FigureRegistry
            registry = FigureRegistry()
            kwargs = registry_kwargs or {}
            accession = registry.register(
                figure_path=path,
                title=kwargs.get("title", metadata.get("title", "") if metadata else ""),
                category=kwargs.get("category", "uncategorized"),
                recipe_name=kwargs.get("recipe_name"),
                data_sources=kwargs.get("data_sources"),
                parameters=kwargs.get("parameters"),
                theme=kwargs.get("theme", "light"),
                mode=metadata.get("mode", "presentation") if metadata else "presentation",
                dpi=dpi,
                script_name=metadata.get("script", "") if metadata else "",
                generation_ms=kwargs.get("generation_ms"),
            )
            return accession
        except Exception as e:
            print(f"  [!] Registry registration failed: {e}", flush=True)
