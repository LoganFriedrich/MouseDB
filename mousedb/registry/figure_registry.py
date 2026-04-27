"""
FigureRegistry - central registry for all generated figures.

Handles registration with full provenance, staleness detection,
querying, and status reporting.
"""

import hashlib
import json
import os
import socket
import struct
from datetime import datetime
from pathlib import Path

from mousedb import MOUSEDB_ROOT
from mousedb.database import get_db
from .models import FigureRecord, FigureDataSource, FigureToolVersion, FigureParameter
from .accession import generate_accession
from .version_capture import capture_versions


class FigureRegistry:
    """Central registry for all generated figures."""

    def __init__(self, db=None):
        """Initialize with optional Database instance.

        Parameters
        ----------
        db : mousedb.database.Database, optional
            Pre-existing database connection. If None, one is created
            on first access via get_db().
        """
        self._db = db

    @property
    def db(self):
        """Lazy-load database connection."""
        if self._db is None:
            self._db = get_db()
        return self._db

    def _compute_method_hash(self, recipe_name, parameters):
        """SHA-256 of recipe_name + sorted JSON params.

        Parameters
        ----------
        recipe_name : str
        parameters : dict

        Returns
        -------
        str
            64-character hex digest.
        """
        payload = json.dumps(
            {"recipe": recipe_name, "params": parameters},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _compute_file_hash(self, file_path):
        """SHA-256 of file content.

        Returns None if the file does not exist.
        """
        path = Path(file_path)
        if not path.exists():
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _get_png_dimensions(file_path):
        """Read width/height from PNG header (bytes 16-24 of IHDR chunk).

        Returns (width, height) or (None, None) if not a valid PNG.
        """
        try:
            with open(file_path, "rb") as f:
                header = f.read(24)
                if header[:8] == b"\x89PNG\r\n\x1a\n":
                    w, h = struct.unpack(">II", header[16:24])
                    return w, h
        except Exception:
            pass
        return None, None

    def _get_file_mtime(self, file_path):
        """Get file modification time as datetime.

        Returns None if the file does not exist.
        """
        path = Path(file_path)
        if not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime)

    def register(
        self,
        figure_path,
        title,
        category="uncategorized",
        recipe_name=None,
        data_sources=None,
        parameters=None,
        theme="light",
        mode="presentation",
        dpi=200,
        script_name=None,
        generation_ms=None,
    ):
        """Register a generated figure with full provenance.

        Parameters
        ----------
        figure_path : str or Path
            Path to the generated figure file.
        title : str
            Human-readable figure title.
        category : str
            Figure category (behavior, tissue, grant, lab_meeting, cross_domain).
        recipe_name : str, optional
            Name of the FigureRecipe that generated this.
        data_sources : list of dict, optional
            Each dict has: source_type, source_path, and optionally
            query_filter, record_count. File hash and mtime are
            computed automatically.
        parameters : dict, optional
            Recipe parameters used (stored as key-value pairs).
        theme : str
            "light", "dark", or "print".
        mode : str
            "presentation", "publication", or "draft".
        dpi : int
            Resolution in dots per inch.
        script_name : str, optional
            Name of the script that generated the figure.
        generation_ms : int, optional
            How long generation took in milliseconds.

        Returns
        -------
        str
            The accession number assigned to this figure.
        """
        figure_path = Path(figure_path)

        # Compute relative path from MOUSEDB_ROOT/figures/
        try:
            rel_path = str(figure_path.relative_to(MOUSEDB_ROOT / "figures"))
        except ValueError:
            rel_path = str(figure_path)

        # Sidecar path
        sidecar = figure_path.with_suffix(".json")
        sidecar_rel = None
        if sidecar.exists():
            try:
                sidecar_rel = str(sidecar.relative_to(MOUSEDB_ROOT / "figures"))
            except ValueError:
                sidecar_rel = str(sidecar)

        # Image dimensions
        width_px, height_px = self._get_png_dimensions(figure_path)

        # Method hash
        method_hash = self._compute_method_hash(
            recipe_name or script_name or "", parameters or {}
        )

        # Capture tool versions
        versions = capture_versions()

        with self.db.session() as session:
            # Generate accession
            accession = generate_accession(session)

            # Mark previous versions of same recipe as not current
            if recipe_name:
                session.query(FigureRecord).filter(
                    FigureRecord.recipe_name == recipe_name,
                    FigureRecord.theme == theme,
                    FigureRecord.is_current == 1,
                ).update({"is_current": 0})

            # Create figure record
            record = FigureRecord(
                accession=accession,
                title=title,
                category=category,
                recipe_name=recipe_name,
                file_path=rel_path,
                sidecar_path=sidecar_rel,
                theme=theme,
                mode=mode,
                dpi=dpi,
                width_px=width_px,
                height_px=height_px,
                method_hash=method_hash,
                is_current=1,
                generated_at=datetime.now(),
                generated_by=socket.gethostname(),
                script_name=script_name or "",
                generation_ms=generation_ms,
            )
            session.add(record)
            session.flush()  # Get the id assigned

            # Add data sources
            for ds in data_sources or []:
                src_path = ds.get("source_path", "")
                source = FigureDataSource(
                    figure_id=record.id,
                    source_type=ds.get("source_type", "unknown"),
                    source_path=src_path,
                    source_hash=self._compute_file_hash(src_path),
                    source_modified=self._get_file_mtime(src_path),
                    record_count=ds.get("record_count"),
                    query_filter=ds.get("query_filter"),
                )
                session.add(source)

            # Add tool versions
            for tool_name, tool_version in versions.items():
                tv = FigureToolVersion(
                    figure_id=record.id,
                    tool_name=tool_name,
                    tool_version=str(tool_version),
                )
                session.add(tv)

            # Add parameters
            for key, value in (parameters or {}).items():
                param = FigureParameter(
                    figure_id=record.id,
                    param_key=key,
                    param_value=json.dumps(value, default=str),
                )
                session.add(param)

            session.commit()
            print(
                "  Registered %s -> %s" % (accession, rel_path),
                flush=True,
            )
            return accession

    def check_staleness(self, recipe_name=None, category=None):
        """Check which current figures have stale data sources.

        A figure is stale if any of its data source files have changed
        (by content hash or modification time) since registration.

        Parameters
        ----------
        recipe_name : str, optional
            Filter to a specific recipe.
        category : str, optional
            Filter to a specific category.

        Returns
        -------
        list of dict
            Each dict has: accession, recipe_name, title, file_path,
            stale_sources (list of dicts with source_path, reason, details).
        """
        stale = []
        with self.db.session() as session:
            query = session.query(FigureRecord).filter(
                FigureRecord.is_current == 1
            )
            if recipe_name:
                query = query.filter(FigureRecord.recipe_name == recipe_name)
            if category:
                query = query.filter(FigureRecord.category == category)

            for record in query.all():
                stale_sources = []
                for ds in record.data_sources:
                    if not ds.source_path:
                        continue
                    current_hash = self._compute_file_hash(ds.source_path)
                    current_mtime = self._get_file_mtime(ds.source_path)

                    if (
                        current_hash
                        and ds.source_hash
                        and current_hash != ds.source_hash
                    ):
                        stale_sources.append(
                            {
                                "source_path": ds.source_path,
                                "reason": "content_changed",
                                "old_hash": ds.source_hash[:12] + "...",
                                "new_hash": current_hash[:12] + "...",
                            }
                        )
                    elif (
                        current_mtime
                        and ds.source_modified
                        and current_mtime > ds.source_modified
                    ):
                        stale_sources.append(
                            {
                                "source_path": ds.source_path,
                                "reason": "mtime_changed",
                                "old_mtime": str(ds.source_modified),
                                "new_mtime": str(current_mtime),
                            }
                        )

                if stale_sources:
                    stale.append(
                        {
                            "accession": record.accession,
                            "recipe_name": record.recipe_name,
                            "title": record.title,
                            "file_path": record.file_path,
                            "stale_sources": stale_sources,
                        }
                    )
        return stale

    def query(self, category=None, recipe_name=None, current_only=True, theme=None):
        """Query registered figures.

        Parameters
        ----------
        category : str, optional
        recipe_name : str, optional
        current_only : bool
            If True (default), only return figures marked is_current=1.
        theme : str, optional

        Returns
        -------
        list of FigureRecord
        """
        with self.db.session() as session:
            q = session.query(FigureRecord)
            if current_only:
                q = q.filter(FigureRecord.is_current == 1)
            if category:
                q = q.filter(FigureRecord.category == category)
            if recipe_name:
                q = q.filter(FigureRecord.recipe_name == recipe_name)
            if theme:
                q = q.filter(FigureRecord.theme == theme)
            return q.order_by(FigureRecord.generated_at.desc()).all()

    def get_latest(self, recipe_name, theme="light"):
        """Get the most recent current figure for a recipe.

        Parameters
        ----------
        recipe_name : str
        theme : str

        Returns
        -------
        FigureRecord or None
        """
        with self.db.session() as session:
            return (
                session.query(FigureRecord)
                .filter(
                    FigureRecord.recipe_name == recipe_name,
                    FigureRecord.theme == theme,
                    FigureRecord.is_current == 1,
                )
                .order_by(FigureRecord.generated_at.desc())
                .first()
            )

    def history(self, recipe_name):
        """Get all versions of a figure recipe (current + archived).

        Parameters
        ----------
        recipe_name : str

        Returns
        -------
        list of FigureRecord
            Ordered by generated_at descending (newest first).
        """
        with self.db.session() as session:
            return (
                session.query(FigureRecord)
                .filter(FigureRecord.recipe_name == recipe_name)
                .order_by(FigureRecord.generated_at.desc())
                .all()
            )

    def status_report(self):
        """Generate a status report of all registered figures.

        Returns
        -------
        dict
            Keys: total_records, current_figures, archived_figures,
            stale_figures, by_category (dict), stale_details (list).
        """
        with self.db.session() as session:
            from sqlalchemy import func

            total = session.query(FigureRecord).count()
            current = (
                session.query(FigureRecord)
                .filter(FigureRecord.is_current == 1)
                .count()
            )

            # Count by category
            cats = (
                session.query(FigureRecord.category, func.count(FigureRecord.id))
                .filter(FigureRecord.is_current == 1)
                .group_by(FigureRecord.category)
                .all()
            )

            stale = self.check_staleness()

            return {
                "total_records": total,
                "current_figures": current,
                "archived_figures": total - current,
                "stale_figures": len(stale),
                "by_category": {cat: count for cat, count in cats},
                "stale_details": stale,
            }
