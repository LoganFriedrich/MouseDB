"""
FigureRecipe base class - declarative figure definitions.

Subclass FigureRecipe to define a specific figure. The base class handles:
- Data loading orchestration
- Theme application
- Methodology panel generation
- Provenance tracking via FigureRegistry
- JSON sidecar export

Each recipe implements: load_data(), analyze(), plot(), methodology_text().
Call recipe.generate() to produce the figure end-to-end.

Multi-panel support:
    Override create_axes() to return a custom axes layout (dict or array).
    The plot() method receives whatever create_axes() returns as the `ax`
    parameter, so multi-panel recipes get their full axes structure.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

from mousedb import MOUSEDB_ROOT, DEFAULT_FIGURES_PATH
from mousedb.figures.standards import apply_style, get_theme, DPI
from mousedb.figures.annotations import add_methodology_panel, add_provenance_footer
from mousedb.figures.export import save_figure
from mousedb.figures.validation import validate_layout, check_readability
from mousedb.figures.legends import FigureLegend, add_figure_legend
from mousedb.registry import FigureRegistry


@dataclass
class DataSource:
    """Declares a data source that a recipe needs.

    Parameters
    ----------
    source_type : str
        Type of source: "csv", "db_table", "h5", "nd2", "json", "parquet".
    source_path : str
        File path (absolute or relative to MOUSEDB_ROOT) or table name.
    query_filter : str, optional
        Description of filtering applied (for provenance).
    """
    source_type: str
    source_path: str
    query_filter: Optional[str] = None

    def resolve_path(self) -> Path:
        """Resolve to absolute path."""
        p = Path(self.source_path)
        if p.is_absolute():
            return p
        return MOUSEDB_ROOT / p

    def to_dict(self) -> dict:
        """Convert to dict for registry registration."""
        return {
            "source_type": self.source_type,
            "source_path": str(self.resolve_path()),
            "query_filter": self.query_filter,
        }


class FigureRecipe(ABC):
    """Base class for declarative figure definitions.

    Subclass and implement the abstract methods to define a new figure.
    Call generate() to produce it with full provenance tracking.

    Class attributes to set:
        name : str           - Unique recipe identifier (e.g., "pellet_score_recovery")
        title : str          - Human-readable title
        category : str       - "behavior", "tissue", "grant", "lab_meeting", "cross_domain"
        data_sources : list  - List of DataSource objects
        default_mode : str   - "presentation" or "publication"
        figsize : tuple      - Default figure size (width, height) in inches

    Multi-panel support:
        Override create_axes() to return custom axes. The plot() method receives
        whatever create_axes() returns as its `ax` parameter.
    """

    name: str = ""
    title: str = ""
    category: str = "uncategorized"
    data_sources: List[DataSource] = []
    default_mode: str = "presentation"
    figsize: tuple = (13, 10)

    @abstractmethod
    def load_data(self) -> Dict[str, Any]:
        """Load and validate all required data.

        Returns
        -------
        dict : Data needed for analysis and plotting.
        """

    @abstractmethod
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Run statistical analysis on loaded data.

        Parameters
        ----------
        data : dict
            Output from load_data().

        Returns
        -------
        dict : Analysis results (stats, aggregations, etc.).
        """

    @abstractmethod
    def plot(self, data: Dict[str, Any], results: Dict[str, Any],
             fig: plt.Figure, ax, theme: str):
        """Create the visualization on the provided axes.

        Parameters
        ----------
        data : dict
            Output from load_data().
        results : dict
            Output from analyze().
        fig : matplotlib Figure
        ax : matplotlib Axes, ndarray of Axes, or dict of Axes
            For single-panel recipes: a single Axes.
            For multi-panel recipes: whatever create_axes() returned.
        theme : str
            Active theme name ("light", "dark", "print").
        """

    @abstractmethod
    def methodology_text(self, data: Dict[str, Any],
                         results: Dict[str, Any]) -> str:
        """Generate methodology panel text.

        Parameters
        ----------
        data : dict
            Output from load_data().
        results : dict
            Output from analyze().

        Returns
        -------
        str : Multi-line methodology text for the info panel.
        """

    def figure_legend(self, data: Dict[str, Any],
                      results: Dict[str, Any]) -> Optional[FigureLegend]:
        """Build a structured figure legend (7-component narrative).

        Override to provide a FigureLegend with: question, method, finding,
        analysis, effect_sizes, confounds, follow_up. Returns None by default
        (backward compatible -- methodology panel still renders).

        Returns
        -------
        FigureLegend or None
        """
        return None

    def get_parameters(self) -> Dict[str, Any]:
        """Return recipe parameters for provenance tracking.

        Override to include tunable parameters like thresholds,
        inclusion criteria, etc.
        """
        return {}

    def create_axes(self, fig: plt.Figure, plot_gs: gridspec.SubplotSpec):
        """Create the plot axes within the given GridSpec slot.

        Override this for multi-panel layouts. The default creates a single
        Axes filling the entire plot area.

        Parameters
        ----------
        fig : matplotlib Figure
        plot_gs : SubplotSpec
            The GridSpec slot allocated for plotting (excludes the
            methodology panel and legend panel at the bottom).

        Returns
        -------
        ax : Axes, ndarray of Axes, or dict of Axes
            Whatever is returned here is passed to plot() as the `ax` param.

        Examples
        --------
        Single panel (default):
            return fig.add_subplot(plot_gs)

        2x2 grid:
            inner_gs = plot_gs.subgridspec(2, 2, hspace=0.3, wspace=0.3)
            axes = np.array([[fig.add_subplot(inner_gs[r, c])
                              for c in range(2)] for r in range(2)])
            return axes

        Named panels:
            inner_gs = plot_gs.subgridspec(1, 2, wspace=0.3)
            return {
                "left": fig.add_subplot(inner_gs[0]),
                "right": fig.add_subplot(inner_gs[1]),
            }
        """
        return fig.add_subplot(plot_gs)

    def generate(self, output_dir=None, theme="light", mode=None,
                 register=True, close=True):
        """Full pipeline: load -> analyze -> plot -> save -> register.

        Parameters
        ----------
        output_dir : str or Path, optional
            Output directory. Defaults to MOUSEDB_ROOT/figures/{category}/.
        theme : str
            "light", "dark", or "print".
        mode : str, optional
            "presentation" or "publication". Defaults to self.default_mode.
        register : bool
            If True, register the figure in FigureRegistry.
        close : bool
            If True, close the figure after saving.

        Returns
        -------
        dict : {"accession": str, "path": Path, "data": dict, "results": dict}
        """
        mode = mode or self.default_mode
        t0 = time.time()

        # Apply style
        apply_style(mode, theme)
        t = get_theme(theme)

        # Resolve output directory
        if output_dir is None:
            output_dir = DEFAULT_FIGURES_PATH / self.category
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load data
        print(f"\n{'=' * 60}", flush=True)
        print(f"RECIPE: {self.title}", flush=True)
        print(f"{'=' * 60}", flush=True)

        print("  Loading data...", flush=True)
        data = self.load_data()

        # Analyze
        print("  Running analysis...", flush=True)
        results = self.analyze(data)

        # Create figure with methodology panel + optional legend
        legend_obj = self.figure_legend(data, results)
        has_legend = legend_obj is not None

        if has_legend:
            # 3 rows: plot area, methodology, legend
            fig = plt.figure(figsize=(self.figsize[0], self.figsize[1] * 1.15))
            outer_gs = gridspec.GridSpec(
                3, 1, height_ratios=[3.5, 1.0, 1.0], hspace=0.22,
            )
            plot_gs = outer_gs[0]
            ax_info = fig.add_subplot(outer_gs[1])
            ax_info.axis("off")
            ax_legend = fig.add_subplot(outer_gs[2])
            ax_legend.axis("off")
        else:
            fig = plt.figure(figsize=self.figsize)
            outer_gs = gridspec.GridSpec(
                2, 1, height_ratios=[3.5, 1.5], hspace=0.22,
            )
            plot_gs = outer_gs[0]
            ax_info = fig.add_subplot(outer_gs[1])
            ax_info.axis("off")

        # Create plot axes (single or multi-panel via override)
        ax = self.create_axes(fig, plot_gs)

        # Plot
        print("  Plotting...", flush=True)
        self.plot(data, results, fig, ax, theme)

        # Layout validation (warnings only, does not block save)
        try:
            fig.canvas.draw()
            layout_warnings = validate_layout(fig)
            readability_warnings = check_readability(fig)
            all_warnings = layout_warnings + readability_warnings
            if all_warnings:
                print(f"  [!] Layout validation: {len(all_warnings)} warning(s):",
                      flush=True)
                for w in all_warnings[:10]:
                    print(f"      - {w}", flush=True)
                if len(all_warnings) > 10:
                    print(f"      ... and {len(all_warnings) - 10} more",
                          flush=True)
        except Exception as e:
            print(f"  [!] Layout validation skipped: {e}", flush=True)

        # Methodology panel
        meth_text = self.methodology_text(data, results)
        prov = add_provenance_footer(
            fig, f"recipe:{self.name}",
            [ds.source_path for ds in self.data_sources],
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        full_text = meth_text.rstrip("\n") + "\n" + prov
        add_methodology_panel(ax_info, full_text, theme=theme)

        # Figure legend (structured narrative)
        if has_legend:
            add_figure_legend(ax_legend, legend_obj, theme=theme)

        # Save
        out_path = output_dir / f"{self.name}.png"
        generation_ms = int((time.time() - t0) * 1000)

        # Build registry kwargs
        registry_kwargs = {
            "title": self.title,
            "category": self.category,
            "recipe_name": self.name,
            "data_sources": [ds.to_dict() for ds in self.data_sources],
            "parameters": self.get_parameters(),
            "theme": theme,
            "generation_ms": generation_ms,
        }

        metadata = {
            "title": self.title,
            "script": f"recipe:{self.name}",
            "data_sources": [ds.source_path for ds in self.data_sources],
            "mode": mode,
            "generated_at": datetime.now().isoformat(),
        }

        dpi = DPI.get(mode, 200)
        facecolor = t["background"]

        save_figure(
            fig, out_path, dpi=dpi, metadata=metadata,
            facecolor=facecolor,
            register=register, registry_kwargs=registry_kwargs,
        )

        if close:
            plt.close(fig)

        print(f"  Saved -> {out_path}", flush=True)
        print(f"  Generated in {generation_ms}ms", flush=True)

        return {
            "path": out_path,
            "data": data,
            "results": results,
            "generation_ms": generation_ms,
        }
