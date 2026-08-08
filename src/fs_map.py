"""fs_map.py — FWL extraction JSON -> FamilySearch GEDCOM X write plan.

Pure and offline: no network, no Flask, no FamilySearch client. Produces a plan of
persons/relationships/source keyed by local temp keys (not real PIDs) — fs_client.py
resolves temp keys to PIDs (or synthetic dry-run PIDs) when it executes the plan.

Mapping rules are per planning/familysearch-upload-plan.md §2. Field/date/place
formats are per docs/data_schema.md.
"""

import re
from urllib.parse import urlparse

GEDCOMX_GIVEN = "http://gedcomx.org/Given"
GEDCOMX_SURNAME = "http://gedcomx.org/Surname"
GEDCOMX_SUFFIX = "http://gedcomx.org/Suffix"
GEDCOMX_BIRTH_NAME = "http://gedcomx.org/BirthName"

GEDCOMX_GENDER_TYPES = {"Male": "http://gedcomx.org/Male", "Female": "http://gedcomx.org/Female"}
GEDCOMX_GENDER_UNKNOWN = "http://gedcomx.org/Unknown"

GEDCOMX_BIRTH = "http://gedcomx.org/Birth"
GEDCOMX_DEATH = "http://gedcomx.org/Death"
GEDCOMX_BURIAL = "http://gedcomx.org/Burial"

# Provisional cap on the Source Description note pending the length-limit check
# against live docs (plan §9 open question 6, deferred to M2.3 build).
SOURCE_NOTE_MAX_CHARS = 10000

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def to_formal_date(date_str: str) -> str | None:
    """'1939-07-25' / '1939-07' / '1939' -> '+1939-07-25' / '+1939-07' / '+1939'."""
    if not date_str:
        return None
    m = _DATE_RE.match(date_str.strip())
    if not m:
        return None
    year, month, day = m.groups()
    formal = f"+{year}"
    if month:
        formal += f"-{month}"
        if day:
            formal += f"-{day}"
    return formal


def _name_parts(given_names: str, surname: str, suffix: str = "") -> list[dict]:
    parts = []
    if given_names:
        parts.append({"type": GEDCOMX_GIVEN, "value": given_names})
    if surname:
        parts.append({"type": GEDCOMX_SURNAME, "value": surname})
    if suffix:
        parts.append({"type": GEDCOMX_SUFFIX, "value": suffix})
    return parts


def build_names(given_names: str, surname: str, suffix: str = "", maiden_name: str = "") -> list[dict]:
    """Preferred name (married/current surname) plus an optional second BirthName
    (maiden surname) per plan §2.1."""
    names = [{"nameForms": [{"parts": _name_parts(given_names, surname, suffix)}]}]
    if maiden_name:
        names.append({
            "type": GEDCOMX_BIRTH_NAME,
            "nameForms": [{"parts": _name_parts(given_names, maiden_name)}],
        })
    return names


def build_gender(gender_str: str) -> dict:
    return {"type": GEDCOMX_GENDER_TYPES.get(gender_str, GEDCOMX_GENDER_UNKNOWN)}


def _date_place_fact(fact_type: str, date_str: str, place_str: str) -> dict | None:
    if not date_str and not place_str:
        return None
    fact = {"type": fact_type}
    if date_str:
        fact["date"] = {"original": date_str}
        formal = to_formal_date(date_str)
        if formal:
            fact["date"]["formal"] = formal
    if place_str:
        fact["place"] = {"original": place_str}
    return fact


def build_subject_person(deceased: dict) -> dict:
    names = build_names(
        deceased.get("given_names", ""),
        deceased.get("surname", ""),
        deceased.get("suffix", ""),
        deceased.get("maiden_name", ""),
    )

    facts = []
    for fact_type, date_field, place_field in (
        (GEDCOMX_BIRTH, "birth_date", "birth_place"),
        (GEDCOMX_DEATH, "death_date", "death_place"),
    ):
        fact = _date_place_fact(fact_type, deceased.get(date_field, ""), deceased.get(place_field, ""))
        if fact:
            facts.append(fact)

    burial_place = deceased.get("burial_place", "")
    if burial_place:
        facts.append({"type": GEDCOMX_BURIAL, "place": {"original": burial_place}})

    return {
        "names": names,
        "gender": build_gender(deceased.get("gender", "")),
        "facts": facts,
        "living": False,
    }


def build_relative_person(rel: dict, *, living: bool, maiden_field: bool = True) -> dict:
    maiden = rel.get("maiden_name", "") if maiden_field else ""
    names = build_names(rel.get("given_names", ""), rel.get("surname", ""), maiden_name=maiden)
    return {"names": names, "living": living}


def _domain_from_url(url: str) -> str:
    netloc = urlparse(url).netloc
    return netloc.removeprefix("www.") if netloc else "newspaper clipping"


def build_source(deceased: dict, source_url: str, raw_text: str, provenance: str = "") -> dict:
    given = deceased.get("given_names", "") or "Unknown"
    surname = deceased.get("surname", "") or "Unknown"
    birth_year = (deceased.get("birth_date", "") or "")[:4] or "?"
    death_year = (deceased.get("death_date", "") or "")[:4] or "?"
    site = _domain_from_url(source_url) if source_url else "newspaper clipping"
    title = f"Obituary of {given} {surname} ({birth_year}–{death_year}), {site}"

    if source_url:
        citation = f'{site}, "{title}", {source_url}'
    else:
        citation = f"{provenance or 'source not recorded'}, obituary of {given} {surname} (pasted text, no URL)"

    note_text = raw_text or ""
    if len(note_text) > SOURCE_NOTE_MAX_CHARS:
        note_text = note_text[:SOURCE_NOTE_MAX_CHARS].rstrip() + f"... [truncated, {len(raw_text)} chars total]"

    source = {
        "titles": [{"value": title}],
        "citations": [{"value": citation}],
        "notes": [{"text": note_text}] if note_text else [],
    }
    if source_url:
        source["about"] = source_url
    return source


def map_extraction_to_plan(data: dict, *, include_living: frozenset = frozenset(), provenance: str = "") -> dict:
    """Build the FamilySearch write plan for one approved extraction JSON.

    `include_living` holds temp keys (e.g. "spouse_0") the reviewing user opted to
    write even though the source didn't mark them deceased — default is exclude
    (plan §2.2 living-relative policy). Returns:
      {
        "persons": {temp_key: gedcomx_person, ...},
        "relationships": {"couples": [...], "child_and_parents": [...]},
        "source": gedcomx_source_description,
        "skipped": [{"key", "role", "reason"}, ...],
      }
    """
    deceased = data.get("deceased", {})
    rels = data.get("relationships", {})

    persons = {"subject": build_subject_person(deceased)}
    skipped = []
    couples = []
    cprs = []

    def _wanted(key: str, is_deceased: bool) -> bool:
        return is_deceased or key in include_living

    for i, sp in enumerate(rels.get("spouses", [])):
        key = f"spouse_{i}"
        is_deceased = bool(sp.get("deceased"))
        if not _wanted(key, is_deceased):
            skipped.append({"key": key, "role": "spouse", "reason": "living, not opted in"})
            continue
        persons[key] = build_relative_person(sp, living=not is_deceased)
        couples.append({"person1": "subject", "person2": key})

    parent_keys: list[str | None] = []
    for i, p in enumerate(rels.get("parents", [])):
        key = f"parent_{i}"
        is_deceased = bool(p.get("deceased"))
        if not _wanted(key, is_deceased):
            skipped.append({"key": key, "role": "parent", "reason": "living, not opted in"})
            parent_keys.append(None)
            continue
        persons[key] = build_relative_person(p, living=not is_deceased)
        parent_keys.append(key)

    parent1 = parent_keys[0] if len(parent_keys) > 0 else None
    parent2 = parent_keys[1] if len(parent_keys) > 1 else None
    parents_created = parent1 is not None or parent2 is not None
    if parents_created:
        cprs.append({"child": "subject", "parent1": parent1, "parent2": parent2})

    for i, c in enumerate(rels.get("children", [])):
        key = f"child_{i}"
        is_deceased = bool(c.get("deceased"))
        if not _wanted(key, is_deceased):
            skipped.append({"key": key, "role": "child", "reason": "living, not opted in"})
            continue
        persons[key] = build_relative_person(c, living=not is_deceased)
        cprs.append({"child": key, "parent1": "subject", "parent2": None})

    # Sibling gating: FS has no direct sibling relationship type — a sibling is only
    # writable as another child of the subject's own parent CPR, so it requires those
    # parent persons to exist in this plan (plan §2.2).
    for i, s in enumerate(rels.get("siblings", [])):
        key = f"sibling_{i}"
        if not parents_created:
            skipped.append({"key": key, "role": "sibling", "reason": "no parent persons in plan — not written to tree"})
            continue
        is_deceased = bool(s.get("deceased"))
        if not _wanted(key, is_deceased):
            skipped.append({"key": key, "role": "sibling", "reason": "living, not opted in"})
            continue
        persons[key] = build_relative_person(s, living=not is_deceased)
        cprs.append({"child": key, "parent1": parent1, "parent2": parent2})

    source = build_source(deceased, data.get("source_url", ""), data.get("raw_text", ""), provenance)

    return {
        "persons": persons,
        "relationships": {"couples": couples, "child_and_parents": cprs},
        "source": source,
        "skipped": skipped,
    }
