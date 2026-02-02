# MouseDB - Installation & Usage

## Quick Setup

```bash
# 1. Create the conda environment
conda env create -f "Y:/2_Connectome/MouseDB/mousedb/environment.yml" -p "Y:/2_Connectome/envs/MouseDB"

# 2. Activate it
conda activate "Y:/2_Connectome/envs/MouseDB"

# 3. Initialize the database
mousedb init

# 4. Import existing Excel data (preview first)
mousedb import --all --dry-run

# 5. Import for real
mousedb import --all

# 6. Launch the GUI
mousedb entry
```

## GUI Features

The GUI has three tabs:

### Tab 1: Pellet Scores
- **For undergrad data entry** (bulletproof validation)
- Select Cohort → Animal → Date
- Weight entry with range validation
- 4 trays × 20 pellets = 80 total
- Click buttons to cycle 0/1/2, or type directly
- Color-coded: Red=Miss, Yellow=Displaced, Green=Retrieved
- Auto-save on "Next Animal"

### Tab 2: Surgery Records
- **For PI data entry** (contusion, tracing, perfusion)
- Select Animal → Fill form → Save
- Shows existing records for the animal
- Contusion: Force (kDyn), Displacement (µm), Velocity (mm/s)
- Tracing: Virus name, Volume (nL)
- Perfusion: Date, Notes

### Tab 3: Dashboard
- **For viewing stats** (like Excel formulas calculated)
- Select Cohort → See overview
- Subject summary table with:
  - Sessions, Pellets scored
  - Miss/Displaced/Retrieved/Contacted %
  - Injury force
- Click subject to see session details:
  - Date, Phase, Days Post-Injury
  - Weight %, Per-session stats

## Commands

| Command | Description |
|---------|-------------|
| `mousedb status` | Show database stats |
| `mousedb init` | Initialize/create database |
| `mousedb new-cohort CNT_06 --start-date 2025-02-01 --mice 16` | Create new cohort |
| `mousedb import --all` | Import all Excel files |
| `mousedb import --file path/to/file.xlsx` | Import single file |
| `mousedb import --all --dry-run` | Validate without importing |
| `mousedb export --cohort CNT_05` | Export to legacy Excel format |
| `mousedb export --cohort CNT_05 --odc` | Export ODC format (calculated stats) |
| `mousedb export --cohort CNT_05 --all-formats` | Export all formats |
| `mousedb export --unified` | Export unified reaches parquet |
| `mousedb entry` | Launch GUI |

## Export Formats

### Legacy Excel (`--cohort CNT_05`)
Matches the old tracking sheet structure:
- `0a_Metadata` - Subject info
- `1_Weight` - Daily weights
- `3b_Manual_Tray` - Pellet scores in 20-column format
- `4_Contusion_Injury_Details` - Surgery info

### ODC Format (`--odc`)
203-column format with calculated stats per session:
- Per-tray: Presented, Miss, Displaced, Retrieved, Contacted (counts + %)
- Daily totals and percentages
- Averages across trays
- Days post-injury, Weight %

### Unified Parquet (`--unified`)
All subjects with session summaries for analysis:
- Subject metadata
- Injury details
- Session-level aggregates

## Files

| Location | Purpose |
|----------|---------|
| `Y:/2_Connectome/MouseDB/connectome.db` | SQLite database (single source of truth) |
| `Y:/2_Connectome/MouseDB/logs/` | Audit trail (JSONL) |
| `Y:/2_Connectome/MouseDB/exports/` | Generated exports |

## Validation Rules

| Field | Constraint | Error |
|-------|------------|-------|
| Subject ID | `CNT_XX_YY` format | "Subject ID must be PROJECT_COHORT_SUBJECT" |
| Pellet score | 0, 1, or 2 only | "Score must be 0=miss, 1=displaced, 2=retrieved" |
| Tray number | 1-4 | "Tray number must be 1-4" |
| Pellet number | 1-20 | "Pellet number must be 1-20" |
| Weight | 10-50g | "Weight must be in valid range" |
| Sex | M or F | "Sex must be M or F" |

## Updating

If you modify the package code:
```bash
# No reinstall needed - it's installed in editable mode (-e)
# Just restart Python/GUI to pick up changes
```

If you add new CLI commands to pyproject.toml:
```bash
conda activate "Y:/2_Connectome/envs/MouseDB"
pip install -e "Y:/2_Connectome/MouseDB/mousedb"
```

## Troubleshooting

### "PyQt5 not found"
```bash
conda activate "Y:/2_Connectome/envs/MouseDB"
pip install PyQt5
```

### "Module not found: mousedb"
```bash
pip install -e "Y:/2_Connectome/MouseDB/mousedb"
```

### Database locked
- Only one user should run `mousedb import` at a time
- GUI can have multiple users reading simultaneously
- Writes are serialized automatically
