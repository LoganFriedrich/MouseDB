<!-- Parent: ../AGENTS.md -->
# mousedb Package Source

> **Type**: CODE
> **Purpose**: Package implementation modules

## Key Modules
| Module | Purpose |
|--------|---------|
| `schema.py` | SQLAlchemy ORM with 17 tables |
| `importers.py` | Data importers (Excel, BrainGlobe) |
| `exporters.py` | Data exporters (Excel, ODC, Parquet) |
| `validators.py` | ID format and data validation |
| `cli.py` | CLI commands |
| `database.py` | Database connection management |
| `gui/` | PyQt5 GUI widgets |
| `cohort_tools/` | Cohort sheet management |

## For AI Agents
- All new features go here as Python modules.
- Follow existing patterns: use SQLAlchemy ORM, validate IDs, log imports.
- ASCII only in print/logging output (Windows console compatibility).
