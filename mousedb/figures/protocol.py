"""
FigureProtocol - Enforces Connectome figure standards.

Wraps matplotlib figure creation to ensure every figure includes required
elements (methodology panel, provenance, sample sizes, proper styling).

Usage:
    from mousedb.figures import FigureProtocol

    fp = FigureProtocol(
        title="Pellet Retrieval Recovery",
        script_name="make_presentation_figures.py",
        data_sources=["pellet_scores.csv"],
    )

    fig, ax, ax_info = fp.create_figure(
        figsize=(12, 9),
        methodology_text="EXPERIMENT  Skilled reaching task...",
    )

    # Plot your data on ax...
    ax.plot(x, y)

    # Save with provenance
    fp.save(fig, "output.png")
"""

from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from .standards import apply_style, DPI
from .annotations import add_methodology_panel, add_provenance_footer
from .export import save_figure


class FigureProtocol:
    """Enforces Connectome figure standards at figure creation and save time.

    Parameters
    ----------
    title : str
        Figure title (shown at top of plot).
    script_name : str
        Name of the script generating this figure (for provenance).
    data_sources : list of str
        Data files used (for provenance).
    mode : str
        "presentation" (default) or "publication". Controls DPI and font sizes.
    """

    def __init__(self, title, script_name, data_sources=None, mode="presentation"):
        self.title = title
        self.script_name = script_name
        self.data_sources = data_sources or []
        self.mode = mode
        self.created_at = datetime.now()
        self._has_methodology = False
        self._has_provenance = False

        # Apply style immediately
        apply_style(mode)

    def create_figure(self, figsize=(12, 9), methodology_text=None,
                      include_info_panel=True, height_ratios=None,
                      n_plot_rows=1, n_plot_cols=1):
        """Create a figure with optional methodology panel.

        Parameters
        ----------
        figsize : tuple
            Figure size in inches (width, height).
        methodology_text : str, optional
            Text for the methodology panel. If provided, the panel is populated.
        include_info_panel : bool
            If True, creates a bottom panel for methodology text.
        height_ratios : list, optional
            Custom height ratios for [plot_area, info_panel].
            Default: [3.5, 1.5] for single row, [3.5, 1.5] for multi-row.
        n_plot_rows : int
            Number of rows in the plot area.
        n_plot_cols : int
            Number of columns in the plot area.

        Returns
        -------
        fig : matplotlib Figure
        axes : matplotlib Axes or array of Axes
            The plot axes. Single Axes if n_plot_rows==n_plot_cols==1,
            otherwise array.
        ax_info : matplotlib Axes or None
            The methodology panel axes (invisible, for text only).
            None if include_info_panel is False.
        """
        if include_info_panel:
            ratios = height_ratios or [3.5, 1.5]
            fig = plt.figure(figsize=figsize)
            gs = gridspec.GridSpec(
                n_plot_rows + 1, n_plot_cols,
                height_ratios=[ratios[0]] * n_plot_rows + [ratios[1]],
                hspace=0.25,
            )

            # Create plot axes
            if n_plot_rows == 1 and n_plot_cols == 1:
                axes = fig.add_subplot(gs[0, :])
            else:
                axes = []
                for r in range(n_plot_rows):
                    row_axes = []
                    for c in range(n_plot_cols):
                        row_axes.append(fig.add_subplot(gs[r, c]))
                    axes.append(row_axes)
                if n_plot_rows == 1:
                    axes = axes[0]  # Flatten single row

            # Info panel spans all columns
            ax_info = fig.add_subplot(gs[-1, :])
            ax_info.axis("off")

            # Add methodology text if provided
            if methodology_text:
                # Append provenance line
                prov = add_provenance_footer(
                    fig, self.script_name, self.data_sources,
                    self.created_at.strftime("%Y-%m-%d %H:%M"),
                )
                full_text = methodology_text.rstrip("\n") + "\n" + prov
                add_methodology_panel(ax_info, full_text)
                self._has_methodology = True
                self._has_provenance = True
        else:
            fig, axes = plt.subplots(
                n_plot_rows, n_plot_cols, figsize=figsize,
            )
            ax_info = None

        return fig, axes, ax_info

    def save(self, fig, path, dpi=None, sidecar=True, close=True):
        """Save figure with provenance metadata.

        Parameters
        ----------
        fig : matplotlib Figure
        path : str or Path
            Output file path.
        dpi : int, optional
            Override DPI. Defaults to mode-appropriate DPI.
        sidecar : bool
            If True, writes a JSON sidecar with provenance metadata.
        close : bool
            If True, closes the figure after saving.
        """
        if dpi is None:
            dpi = DPI.get(self.mode, 200)

        path = Path(path)

        metadata = {
            "title": self.title,
            "script": self.script_name,
            "data_sources": self.data_sources,
            "generated_at": self.created_at.isoformat(),
            "mode": self.mode,
            "dpi": dpi,
        }

        save_figure(fig, path, dpi=dpi, metadata=metadata, sidecar=sidecar)

        if close:
            plt.close(fig)

        print(f"  Saved -> {path}", flush=True)
        return path
