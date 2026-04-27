"""
Figure Registry ORM models — re-exported from mousedb.schema.

The canonical definitions live in mousedb.schema (so Base.metadata.create_all()
picks them up automatically). This module re-exports them for convenient
imports within the registry package.
"""

from mousedb.schema import (  # noqa: F401
    FigureRecord,
    FigureDataSource,
    FigureToolVersion,
    FigureParameter,
)
