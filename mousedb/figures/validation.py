"""
Post-render layout validation for Connectome figures.

Checks for overlapping text, out-of-bounds elements, and readability
issues. Called by FigureRecipe.generate() after plotting but before save.
"""

import matplotlib.pyplot as plt
from matplotlib.figure import Figure


def validate_layout(fig: Figure) -> list:
    """Check figure for layout problems after rendering.

    Must call fig.canvas.draw() or fig.savefig() first so that text
    bounding boxes are computed.

    Parameters
    ----------
    fig : matplotlib Figure
        The rendered figure to validate.

    Returns
    -------
    list of str : Warning messages. Empty list means all clear.
    """
    warnings = []

    # Force a draw so bounding boxes are computed
    renderer = fig.canvas.get_renderer()

    # Collect all visible Text artists from figure and all axes
    texts = []
    for text in fig.texts:
        if text.get_visible() and text.get_text().strip():
            texts.append(text)
    for ax in fig.get_axes():
        for text in ax.texts:
            if text.get_visible() and text.get_text().strip():
                texts.append(text)
        # Include title, axis labels
        if ax.get_title():
            texts.append(ax.title)
        if ax.get_xlabel():
            texts.append(ax.xaxis.label)
        if ax.get_ylabel():
            texts.append(ax.yaxis.label)

    # Check for overlapping text bounding boxes
    bboxes = []
    for text in texts:
        try:
            bbox = text.get_window_extent(renderer=renderer)
            bboxes.append((text, bbox))
        except Exception:
            continue

    fig_bbox = fig.get_window_extent(renderer=renderer)

    # Check for text extending beyond figure bounds
    for text, bbox in bboxes:
        content = text.get_text()[:30]
        if bbox.x0 < fig_bbox.x0 - 5:
            warnings.append(f"Text extends beyond left edge: '{content}...'")
        if bbox.x1 > fig_bbox.x1 + 5:
            warnings.append(f"Text extends beyond right edge: '{content}...'")
        if bbox.y0 < fig_bbox.y0 - 5:
            warnings.append(f"Text extends beyond bottom edge: '{content}...'")
        if bbox.y1 > fig_bbox.y1 + 5:
            warnings.append(f"Text extends beyond top edge: '{content}...'")

    # Check for overlapping text pairs
    # Only check significant overlaps (>30% area overlap)
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            text_i, bbox_i = bboxes[i]
            text_j, bbox_j = bboxes[j]

            # Skip tiny text (tick labels overlap is often fine)
            if bbox_i.width < 5 or bbox_j.width < 5:
                continue

            overlap = _bbox_overlap_fraction(bbox_i, bbox_j)
            if overlap > 0.3:
                content_i = text_i.get_text()[:20]
                content_j = text_j.get_text()[:20]
                warnings.append(
                    f"Overlapping text ({overlap:.0%}): "
                    f"'{content_i}...' and '{content_j}...'"
                )

    return warnings


def check_readability(fig: Figure, target_dpi=200, min_fontsize_pt=6) -> list:
    """Warn if any text would be too small at target DPI.

    Parameters
    ----------
    fig : matplotlib Figure
    target_dpi : int
        Target output DPI.
    min_fontsize_pt : float
        Minimum acceptable font size in points.

    Returns
    -------
    list of str : Warning messages.
    """
    warnings = []

    for ax in fig.get_axes():
        for text in ax.texts:
            if text.get_visible() and text.get_fontsize() < min_fontsize_pt:
                content = text.get_text()[:30]
                warnings.append(
                    f"Text too small ({text.get_fontsize():.1f}pt < {min_fontsize_pt}pt): "
                    f"'{content}...'"
                )

    # Check tick labels
    for ax in fig.get_axes():
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            if label.get_visible() and label.get_fontsize() < min_fontsize_pt:
                warnings.append(
                    f"Tick label too small ({label.get_fontsize():.1f}pt): "
                    f"'{label.get_text()[:20]}'"
                )

    return warnings


def _bbox_overlap_fraction(bbox1, bbox2) -> float:
    """Compute overlap fraction between two bounding boxes.

    Returns the overlap area divided by the smaller bbox area.
    """
    x_left = max(bbox1.x0, bbox2.x0)
    y_bottom = max(bbox1.y0, bbox2.y0)
    x_right = min(bbox1.x1, bbox2.x1)
    y_top = min(bbox1.y1, bbox2.y1)

    if x_right <= x_left or y_top <= y_bottom:
        return 0.0

    overlap_area = (x_right - x_left) * (y_top - y_bottom)
    smaller_area = min(bbox1.width * bbox1.height, bbox2.width * bbox2.height)

    if smaller_area <= 0:
        return 0.0

    return overlap_area / smaller_area
