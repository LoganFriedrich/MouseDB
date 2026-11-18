"""Tracing surgeries that inject several viruses at more than one site.

WHY THIS EXISTS (2026-11-03)
----------------------------
The older tracking-sheet layout has one injection tab, ``5_SC_Injection_Details``,
with ONE ``Injected_Virus`` and ONE ``Virus_Titer`` per animal, and a
``0_Injection_Calculations`` tab laid out as dated batch blocks (or older
calculators). A newer layout records a surgery that injects at two sites in the
same sitting -- a spinal cord site and a brain site -- with a mix of several
viruses at each:

* one ``5_<SITE>_Injection_Details`` tab per site (``5_SC_...``, ``5_BR_...``),
  each with ``Injected_Virus_1`` .. ``Injected_Virus_N`` and
  ``Virus_Titer_1`` .. ``Virus_Titer_N`` columns and a ``Total_Viral_Titer``;
* ``0_Injection_Calculations`` as ONE flat table, one row per virus per site:
  ``Virus Name``, ``uL of This Virus``, ``Total Volume``, the concentrations,
  ``Final Concentration in SciNot``, ``Total Viral Load``, ``Intended Location``
  (which site the row's mix is for), ``Mice QTY``, ``Virus Source`` and
  ``Prep_Date`` (the day that site's mix was made);
* ``0_Aliquot_Calculations``: how each raw stock was diluted into aliquots.

Both readers of a workbook -- the sheet importer (``importers.ExcelImporter``,
which writes the surgeries, their injections and the virus preps) and
``animal_records`` (which copies every column for the ODC export) -- read the
site tabs through THIS module, so they cannot disagree about which animal a row
is, which day it happened, or which viruses went in.

WHAT A PERSON TYPES ONCE, AND WHAT IS FILLED FROM ELSEWHERE
-----------------------------------------------------------
The lab rule (2026-11-03) is that a value collected once is typed once. So a
cell on a site tab may hold, instead of a value, a NOTE pointing at where the
value lives -- and a sheet made by mousedb holds a formula that copies it:

* ``Same as 5_SC_Injection_Details ...`` in the animal, date or weight column:
  the two sites are ONE surgery, so the animal, the day and the weight on the
  day are the other site tab's, row for row. A note in the first animal row is
  read as a note for the whole column (that is how sheets are drafted).
* ``From 0_Injection_Calculations`` (or a blank) in the virus and titre
  columns: every animal at that site got the site's mix, so the viruses and
  titres come from the injection table's rows for that site.

A blank animal, date or weight cell is NOT filled from the other site: only an
explicit note (or a formula, whose value is simply read) says the two are the
same surgery. WHY: the weight on the day is the proof a surgery happened
(``animal_records.surgery_verdict``); filling a blank one from another tab would
declare an injection done that nobody recorded.

WHICH TABLE ROWS ARE A SITE'S MIX
---------------------------------
A table row belongs to a site when its ``Intended Location`` names the site's
``Injection_Target`` (the target as a whole word inside the location, e.g. a
target ``C5`` in a location ``CORD (C5)``), or, failing that, names the site by
its kind (a ``5_SC_`` tab: "spinal" or "cord"; a ``5_BR_`` tab: "brain" or
"cranial"). A row is IN the mix when its ``uL of This Virus`` is a positive
number: a virus listed at 0 uL was not injected (a mix is often balanced by
changing which viruses get volume). Anything that cannot be decided this way is
a warning naming the cell to fix -- never a guess.

ASCII-only text (warnings reach Windows consoles).
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

CALC_TAB = "0_Injection_Calculations"
ALIQUOT_TAB = "0_Aliquot_Calculations"
SITE_TAB_RE = re.compile(r"^5_([A-Z]+)_Injection_Details$")

ID_HEADINGS = ("Subject_ID", "Animal", "SubjectID", "Mouse ID")
DATE_HEADINGS = ("Surgery_Date", "Date")
WEIGHT_HEADINGS = ("Subject_Weight (g)", "Subject_Weight_g", "Subject_Weight",
                   "Weight (g)", "Weight_g")
SHARED_HEADINGS = ID_HEADINGS + DATE_HEADINGS + WEIGHT_HEADINGS
# Columns of one site that are the surgery's, not the site's: the same animal
# under the same anaesthesia, so a note may point them at the other site too.
SURVIVAL_PREFIX = "survived"

# A site tab's kind -> words that name it in an Intended Location. Anatomy, not
# a lab fact: any lab's spinal cord site is a cord site.
SITE_WORDS = {"SC": ("spinal", "cord"), "BR": ("brain", "cranial")}

VIRUS_RE = re.compile(r"^injected_virus_(\d+)$", re.I)
TITER_RE = re.compile(r"^virus_titer_(\d+)\b", re.I)
_POINTER_RE = re.compile(r"^\s*(same\s+as|from)\s+\S", re.I)


def _ascii(text: str) -> str:
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def site_tabs(sheetnames: Sequence[str]) -> List[str]:
    """The workbook's per-site injection tabs, in workbook order."""
    return [t for t in sheetnames if SITE_TAB_RE.match(str(t))]


def site_code(tab: str) -> str:
    m = SITE_TAB_RE.match(tab)
    return m.group(1) if m else ""


def is_pointer(v) -> bool:
    """True for a note that says where a value lives instead of holding it
    ('From 0_Injection_Calculations', 'Same as 5_SC_Injection_Details ...')."""
    return isinstance(v, str) and bool(_POINTER_RE.match(v))


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    try:
        return bool(v != v)          # NaN
    except TypeError:
        return False


def _empty_ref(v) -> bool:
    """A cell that holds nothing for an id, date or weight: blank, or the 0 a
    formula pointing at an empty cell shows."""
    return _blank(v) or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0)


def _number(v) -> Optional[float]:
    if isinstance(v, bool) or _blank(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(",", "")
    m = re.fullmatch(r"([0-9.]+)\s*[xX*]\s*10\s*\^\s*([0-9]+)", t)
    if m:
        try:
            return float(m.group(1)) * 10 ** int(m.group(2))
        except ValueError:
            return None
    try:
        return float(t)
    except ValueError:
        return None


def _as_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v.strip():
        t = v.strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(t, fmt).date()
            except ValueError:
                continue
    return None


def _text(v) -> Optional[str]:
    if _blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (datetime, date)):
        return (v.date() if isinstance(v, datetime) else v).isoformat()
    return str(v).strip()


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


# ---------------------------------------------------------------------------
# the injection table
# ---------------------------------------------------------------------------

@dataclass
class TableRow:
    sheet_row: int
    virus: str
    box: Optional[str]
    ul: Optional[float]
    total_volume: Optional[float]
    start: Optional[float]
    target: Optional[float]
    final: Optional[float]
    final_scinot: Optional[float]
    total_load: Optional[float]
    location: Optional[str]
    mice: Optional[float]
    source: Optional[str]
    prep_date: Optional[date]
    prep_date_cell: object = None

    @property
    def in_mix(self) -> bool:
        return self.ul is not None and self.ul > 0


@dataclass
class InjectionTable:
    rows: List[TableRow] = field(default_factory=list)
    has_prep_date_column: bool = False

    def groups(self) -> "OrderedDict[str, List[TableRow]]":
        """Rows by site, in sheet order: {normalised location: rows}."""
        out: "OrderedDict[str, List[TableRow]]" = OrderedDict()
        for r in self.rows:
            if r.location:
                out.setdefault(_norm(r.location), []).append(r)
        return out


def is_flat_table(rows: Sequence[Sequence]) -> bool:
    """True when the tab's FIRST row is the flat table's header: a 'Virus Name'
    column and an 'Intended Location' column."""
    if not rows:
        return False
    head = [_norm(v) for v in rows[0]]
    return any("virus name" in h for h in head) and any("intended location" in h for h in head)


def parse_table(rows: Sequence[Sequence], warnings: List[str]) -> Optional[InjectionTable]:
    """The flat injection table, or None when the tab is not in that layout.
    ``rows``: the tab's cells as typed (row 1 first), blanks as None or ''."""
    if not is_flat_table(rows):
        return None
    head = [_norm(v) for v in rows[0]]

    def col(*words, exclude=()):
        for i, h in enumerate(head):
            if all(w in h for w in words) and not any(x in h for x in exclude):
                return i
        return None

    c = {
        "virus": col("virus name"), "box": col("box"),
        "ul": col("ul of"), "total_volume": col("total volume"),
        "start": col("starting", exclude=("scinot",)), "target": col("target"),
        "final": col("final", exclude=("scinot",)), "final_scinot": col("final", "scinot"),
        "total_load": col("total", "load"), "location": col("intended location"),
        "mice": col("mice"), "source": col("source"), "prep_date": col("prep", "date"),
    }
    table = InjectionTable(has_prep_date_column=c["prep_date"] is not None)

    def cell(r, key):
        i = c[key]
        if i is None or i >= len(r):
            return None
        v = r[i]
        return None if _blank(v) else v

    for n, r in enumerate(rows[1:], start=2):
        name = _text(cell(r, "virus"))
        if not name or name.startswith("<") or name.startswith("["):
            continue                       # blank row, or an unfilled placeholder
        raw_date = cell(r, "prep_date")
        row = TableRow(
            sheet_row=n, virus=name, box=_text(cell(r, "box")),
            ul=_number(cell(r, "ul")), total_volume=_number(cell(r, "total_volume")),
            start=_number(cell(r, "start")), target=_number(cell(r, "target")),
            final=_number(cell(r, "final")), final_scinot=_number(cell(r, "final_scinot")),
            total_load=_number(cell(r, "total_load")), location=_text(cell(r, "location")),
            mice=_number(cell(r, "mice")), source=_text(cell(r, "source")),
            prep_date=_as_date(raw_date), prep_date_cell=raw_date)
        if not row.location:
            warnings.append(_ascii(
                "%s: row %d (%s) has no Intended Location, so no site's mix includes it; "
                "type the site it was made for" % (CALC_TAB, n, name)))
        elif row.ul is None:
            warnings.append(_ascii(
                "%s: row %d (%s) has no 'uL of This Virus', so it is not counted as part "
                "of the %s mix; type its volume (0 if it was left out)" % (CALC_TAB, n, name, row.location)))
        if raw_date is not None and row.prep_date is None:
            warnings.append(_ascii(
                "%s: row %d: the Prep_Date cell reads %s, which is not a date" % (CALC_TAB, n, ascii(raw_date))))
        table.rows.append(row)
    return table


@dataclass
class Prep:
    """One mix-making day: the sites whose mixes were made on it."""
    prep_date: date
    sites: "OrderedDict[str, List[TableRow]]"

    @property
    def viruses(self) -> List[TableRow]:
        return [r for rows in self.sites.values() for r in rows if r.in_mix]


def preps(table: InjectionTable, warnings: List[str]) -> List[Prep]:
    """The table's mixes grouped by the day they were made.

    A site's mix has ONE prep date. Each site's rows are read for it; a site
    with no date, or with two different dates, is named for a person and left
    out (its date is the prep record's key, see importers). Sites made on the
    same day are one prep (the database keeps one prep per cohort per day)."""
    by_day: "OrderedDict[date, OrderedDict]" = OrderedDict()
    for loc, rows in table.groups().items():
        label = rows[0].location
        mix = [r for r in rows if r.in_mix]
        if not mix:
            continue
        days = sorted({r.prep_date for r in rows if r.prep_date is not None})
        if not days:
            warnings.append(_ascii(
                "%s: the %s mix has no Prep_Date, so it is not recorded as a virus prep "
                "(the date is how a prep is told apart from an earlier cohort's); type the "
                "day the mix was made%s" % (
                    CALC_TAB, label,
                    "" if table.has_prep_date_column else " in a 'Prep_Date' column")))
            continue
        if len(days) > 1:
            warnings.append(_ascii(
                "%s: the %s mix has more than one Prep_Date (%s); one mix is made on one day, "
                "so it is not recorded until its rows agree" % (
                    CALC_TAB, label, ", ".join(d.isoformat() for d in days))))
            continue
        by_day.setdefault(days[0], OrderedDict())[label] = rows
    return [Prep(d, s) for d, s in by_day.items()]


def read_aliquots(rows: Sequence[Sequence]) -> List[Dict]:
    """0_Aliquot_Calculations: one dict per virus row (name, box, raw titre,
    ratio, resulting titre). Header in row 1."""
    if not rows:
        return []
    head = [_norm(v) for v in rows[0]]

    def col(*words):
        return next((i for i, h in enumerate(head) if all(w in h for w in words)), None)

    c = {"name": col("virus name"), "box": col("box"), "raw": col("raw stock"),
         "ratio": col("ratio"), "result": col("resulting")}
    out = []
    for r in rows[1:]:
        def get(k):
            i = c[k]
            return r[i] if i is not None and i < len(r) and not _blank(r[i]) else None
        name = _text(get("name"))
        if not name or name.startswith("<"):
            continue
        out.append({"name": name, "box": _text(get("box")), "raw": _number(get("raw")),
                    "ratio": _text(get("ratio")), "result": _number(get("result"))})
    return out


def aliquot_for(virus: str, aliquots: List[Dict]) -> Optional[Dict]:
    """The aliquot row a table virus was drawn from: the aliquot's name is the
    table name without a trailing bracketed note ('X [Protect]' -> 'X')."""
    base = _norm(re.sub(r"\s*\[[^\]]*\]\s*$", "", virus))
    hits = [a for a in aliquots if _norm(a["name"]) == base]
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# the site tabs
# ---------------------------------------------------------------------------

@dataclass
class Injection:
    slot: int
    virus_name: Optional[str]
    titer: Optional[float]
    titer_text: Optional[str]


@dataclass
class SiteRow:
    tab: str
    sheet_row: int
    cells: Dict[str, object]           # heading -> value, notes resolved
    injections: List[Injection]
    total_titer: Optional[float]
    target: Optional[str]
    depths: Optional[str]
    ml: Optional[str]
    ap: Optional[str]


@dataclass
class SiteLayout:
    tabs: List[str]
    headers: Dict[str, List[str]]
    rows: Dict[str, List[SiteRow]]
    table: Optional[InjectionTable]
    warnings: List[str]

    def resolved_rows(self, tab: str) -> Tuple[List[str], List[List]]:
        """(header, rows) of one site tab with every note replaced by the value
        it points at -- what animal_records copies."""
        head = self.headers[tab]
        return head, [[sr.cells.get(h) for h in head] for sr in self.rows[tab]]


def is_multi_site(tabs_rows: Dict[str, Sequence[Sequence]]) -> bool:
    """True when the workbook's site tabs are in the newer layout: more than one
    site tab, or numbered virus columns (Injected_Virus_1 ...)."""
    tabs = site_tabs(list(tabs_rows))
    if len(tabs) > 1:
        return True
    for t in tabs:
        rows = tabs_rows[t]
        if rows and any(VIRUS_RE.match(str(h or "").strip()) for h in rows[0]):
            return True
    return False


def _once(warnings: List[str], seen: set, key, text: str) -> None:
    if key not in seen:
        seen.add(key)
        warnings.append(_ascii(text))


def _heading_index(head: List[str], names: Sequence[str]) -> Optional[int]:
    return next((head.index(n) for n in names if n in head), None)


def _site_groups_for(tab: str, target: Optional[str], table: InjectionTable
                     ) -> Tuple[Optional[List[TableRow]], str]:
    """(the table rows of this site, '' ) or (None, why not)."""
    groups = table.groups()
    if not groups:
        return None, "the injection table has no rows with an Intended Location"
    if target:
        pat = re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(_norm(target)))
        hits = [k for k in groups if pat.search(k)]
        if len(hits) == 1:
            return groups[hits[0]], ""
        if len(hits) > 1:
            return None, ("its target %r appears in more than one Intended Location (%s)"
                          % (target, ", ".join(groups[k][0].location for k in hits)))
    words = SITE_WORDS.get(site_code(tab), ())
    hits = [k for k in groups if any(w in k for w in words)]
    if len(hits) == 1:
        return groups[hits[0]], ""
    return None, ("no single Intended Location names it (its target is %s; locations: %s)"
                  % (repr(target) if target else "blank",
                     ", ".join(rows[0].location for rows in groups.values())))


def read_sites(tabs_rows: Dict[str, Sequence[Sequence]]) -> Optional[SiteLayout]:
    """Read every site tab (and the injection table) of a workbook in the newer
    layout; None when the workbook is in the older one-site layout.

    ``tabs_rows``: {tab name: its cells as typed, row 1 = header}. Pass every
    tab of the workbook (or at least the site tabs and 0_Injection_Calculations).
    """
    if not is_multi_site(tabs_rows):
        return None
    warnings: List[str] = []
    tabs = site_tabs(list(tabs_rows))
    table = parse_table(tabs_rows.get(CALC_TAB) or [], warnings) if CALC_TAB in tabs_rows else None
    headers: Dict[str, List[str]] = {}
    raw: Dict[str, List[List]] = {}
    for t in tabs:
        rows = list(tabs_rows[t] or [])
        headers[t] = [str(h).strip() if not _blank(h) else "" for h in (rows[0] if rows else [])]
        body = [list(r) + [None] * (len(headers[t]) - len(r)) for r in rows[1:]]
        raw[t] = body

    # A note in a column's first animal row speaks for the whole column.
    column_note: Dict[Tuple[str, str], bool] = {}
    for t in tabs:
        first = next((r for r in raw[t] if any(not _blank(v) for v in r)), None)
        for i, h in enumerate(headers[t]):
            column_note[(t, h)] = bool(first is not None and is_pointer(first[i]))

    def partner_value(t: str, i: int, heading: str):
        """The same cell on another site tab's row i, if it holds a value.
        'Survived' and 'Survived (Y/N)' are the same answer under two headings."""
        survival = heading.lower().startswith(SURVIVAL_PREFIX)
        for other in tabs:
            if other == t:
                continue
            hh = heading if heading in headers[other] else (
                next((x for x in headers[other] if x.lower().startswith(SURVIVAL_PREFIX)), None)
                if survival else None)
            if hh is None or i >= len(raw[other]):
                continue
            v = raw[other][i][headers[other].index(hh)]
            if not _empty_ref(v) and not is_pointer(v):
                return v
        return None

    # How many rows each tab has: a tab whose animal column is a note has as
    # many rows as the tab it points at.
    n_rows = {t: len(raw[t]) for t in tabs}
    for t in tabs:
        idc = _heading_index(headers[t], ID_HEADINGS)
        if idc is not None and column_note[(t, headers[t][idc])]:
            n_rows[t] = max([n_rows[t]] + [len(raw[o]) for o in tabs if o != t])

    out: Dict[str, List[SiteRow]] = {}
    seen_why: set = set()
    for t in tabs:
        head = headers[t]
        slots = sorted({int(VIRUS_RE.match(h).group(1)) for h in head if VIRUS_RE.match(h)})
        titer_col = {int(TITER_RE.match(h).group(1)): h for h in head if TITER_RE.match(h)}
        virus_col = {int(VIRUS_RE.match(h).group(1)): h for h in head if VIRUS_RE.match(h)}
        total_h = next((h for h in head if h.lower().startswith("total_viral_titer")), None)
        target_h = next((h for h in head if h in ("Injection_Target", "Injection_Site")), None)
        depth_h = next((h for h in head if h.lower().startswith("depths")), None)
        ml_h = next((h for h in head if h.lower().startswith("coordinates")), None)
        ap_h = next((h for h in head if h.strip().upper() == "A/P"), None)
        rows_out: List[SiteRow] = []
        for i in range(n_rows[t]):
            src = raw[t][i] if i < len(raw[t]) else [None] * len(head)
            cells: Dict[str, object] = {}
            for k, h in enumerate(head):
                if not h:
                    continue
                v = src[k] if k < len(src) else None
                note = is_pointer(v) or (column_note[(t, h)] and _blank(v))
                if note:
                    # The surgery's own columns come from the other site's row;
                    # the virus columns are filled from the table below.
                    shared = h in SHARED_HEADINGS or h.lower().startswith(SURVIVAL_PREFIX)
                    v = partner_value(t, i, h) if shared else None
                elif h in SHARED_HEADINGS and _empty_ref(v) and isinstance(v, (int, float)):
                    v = None                   # the 0 a formula shows for an empty cell
                cells[h] = v
            if not any(not _blank(cells.get(h)) for h in head if h):
                continue                       # an empty row under the table
            target = _text(cells.get(target_h)) if target_h else None
            # Viruses: the row's own when any virus cell holds a name; else the
            # site's mix from the injection table.
            own = [s for s in slots if not _blank(cells.get(virus_col[s]))]
            injections: List[Injection] = []
            total = _number(cells.get(total_h)) if total_h else None
            mix: Optional[List[TableRow]] = None
            why = ""
            if table is not None:
                mix, why = _site_groups_for(t, target, table)
            if own:
                for s in own:
                    name = _text(cells[virus_col[s]])
                    tcell = cells.get(titer_col.get(s)) if s in titer_col else None
                    titer = _number(tcell)
                    if titer is None and mix:
                        match = [r for r in mix if r.in_mix and _norm(r.virus) == _norm(name)]
                        if len(match) == 1:
                            titer = match[0].final_scinot
                            if titer_col.get(s):
                                cells[titer_col[s]] = titer
                    injections.append(Injection(s, name, titer, _text(tcell) if titer is None
                                                else _text(tcell) or _text(titer)))
            elif slots:
                if mix is None:
                    if table is None:
                        why = "the workbook has no %s table in the flat layout" % CALC_TAB
                    _once(warnings, seen_why, (t, why),
                          "%s: the viruses are to come from %s, but %s; name the site in that "
                          "table's Intended Location (or type the viruses on this tab)"
                          % (t, CALC_TAB, why))
                else:
                    in_mix = [r for r in mix if r.in_mix]
                    if len(in_mix) > len(slots):
                        _once(warnings, seen_why, (t, "slots"),
                            "%s: the %s mix has %d viruses but the tab has only %d virus "
                            "columns; the first %d are recorded -- add Injected_Virus_%d.."
                            % (t, mix[0].location, len(in_mix), len(slots), len(slots),
                               len(slots) + 1))
                    for s, r in zip(slots, in_mix):
                        cells[virus_col[s]] = r.virus
                        if s in titer_col:
                            cells[titer_col[s]] = r.final_scinot
                        injections.append(Injection(s, r.virus, r.final_scinot,
                                                    _text(r.final_scinot)))
                    if total is None and in_mix:
                        total = in_mix[0].total_load
                        if total_h:
                            cells[total_h] = total
            rows_out.append(SiteRow(
                tab=t, sheet_row=i + 2, cells=cells, injections=injections,
                total_titer=total, target=target,
                depths=_text(cells.get(depth_h)) if depth_h else None,
                ml=_text(cells.get(ml_h)) if ml_h else None,
                ap=_text(cells.get(ap_h)) if ap_h else None))
        out[t] = rows_out
    return SiteLayout(tabs=tabs, headers=headers, rows=out, table=table, warnings=warnings)
