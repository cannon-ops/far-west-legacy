"""tests/test_fs_map.py — Unit tests for src/fs_map.py (pure, offline)."""

from src.fs_map import (
    GEDCOMX_BIRTH,
    GEDCOMX_BIRTH_NAME,
    GEDCOMX_DEATH,
    build_names,
    build_source,
    build_subject_person,
    map_extraction_to_plan,
    to_formal_date,
)


class TestToFormalDate:
    def test_full_date(self):
        assert to_formal_date("1939-07-25") == "+1939-07-25"

    def test_year_month(self):
        assert to_formal_date("1939-07") == "+1939-07"

    def test_year_only(self):
        assert to_formal_date("1939") == "+1939"

    def test_empty_string(self):
        assert to_formal_date("") is None

    def test_garbage_input(self):
        assert to_formal_date("not a date") is None


class TestBuildNames:
    def test_given_and_surname_only(self):
        names = build_names("Donna Sue", "Neese")
        assert len(names) == 1
        parts = names[0]["nameForms"][0]["parts"]
        assert {"type": "http://gedcomx.org/Given", "value": "Donna Sue"} in parts
        assert {"type": "http://gedcomx.org/Surname", "value": "Neese"} in parts

    def test_suffix_included_when_present(self):
        names = build_names("James", "Neese", suffix="Jr.")
        parts = names[0]["nameForms"][0]["parts"]
        assert {"type": "http://gedcomx.org/Suffix", "value": "Jr."} in parts

    def test_suffix_omitted_when_empty(self):
        names = build_names("James", "Neese", suffix="")
        parts = names[0]["nameForms"][0]["parts"]
        assert not any(p["type"] == "http://gedcomx.org/Suffix" for p in parts)

    def test_maiden_name_adds_second_birth_name(self):
        names = build_names("Nellie", "Neese", maiden_name="Walker")
        assert len(names) == 2
        assert names[1]["type"] == GEDCOMX_BIRTH_NAME
        maiden_parts = names[1]["nameForms"][0]["parts"]
        assert {"type": "http://gedcomx.org/Surname", "value": "Walker"} in maiden_parts
        # Preferred (first) name keeps the married surname
        preferred_parts = names[0]["nameForms"][0]["parts"]
        assert {"type": "http://gedcomx.org/Surname", "value": "Neese"} in preferred_parts

    def test_no_maiden_name_yields_single_name(self):
        names = build_names("Andrew", "Neese", maiden_name="")
        assert len(names) == 1


class TestBuildSubjectPerson:
    def test_full_dates_and_places(self):
        deceased = {
            "given_names": "Donna Sue", "surname": "Neese", "maiden_name": "", "suffix": "",
            "gender": "Female", "birth_date": "1939-07-25", "birth_place": "Jamesport, Missouri",
            "death_date": "2025-12-10", "death_place": "Pleasant Valley, Missouri", "burial_place": "",
        }
        person = build_subject_person(deceased)
        assert person["gender"] == {"type": "http://gedcomx.org/Female"}
        assert person["living"] is False
        birth = next(f for f in person["facts"] if f["type"] == GEDCOMX_BIRTH)
        assert birth["date"]["formal"] == "+1939-07-25"
        assert birth["place"]["original"] == "Jamesport, Missouri"
        death = next(f for f in person["facts"] if f["type"] == GEDCOMX_DEATH)
        assert death["date"]["formal"] == "+2025-12-10"

    def test_empty_birth_fact_omitted(self):
        deceased = {
            "given_names": "X", "surname": "Y", "gender": "Unknown",
            "birth_date": "", "birth_place": "", "death_date": "2025-01-01", "death_place": "",
        }
        person = build_subject_person(deceased)
        assert not any(f["type"] == GEDCOMX_BIRTH for f in person["facts"])

    def test_burial_fact_only_when_place_present(self):
        deceased = {"given_names": "X", "surname": "Y", "gender": "Unknown", "burial_place": "Jamesport Cemetery"}
        person = build_subject_person(deceased)
        burial = next(f for f in person["facts"] if f["type"] == "http://gedcomx.org/Burial")
        assert burial["place"]["original"] == "Jamesport Cemetery"
        assert "date" not in burial

    def test_unknown_gender_maps_to_gedcomx_unknown(self):
        deceased = {"given_names": "X", "surname": "Y", "gender": ""}
        person = build_subject_person(deceased)
        assert person["gender"] == {"type": "http://gedcomx.org/Unknown"}


class TestSiblingGating:
    BASE = {
        "deceased": {"given_names": "Donna Sue", "surname": "Neese", "gender": "Female"},
        "relationships": {
            "spouses": [], "parents": [], "children": [],
            "siblings": [{"given_names": "Mavis", "surname": "Neese", "maiden_name": "", "deceased": True}],
        },
        "eulogy_text": "", "service_details": "", "source_url": "", "raw_text": "obituary text",
    }

    def test_siblings_skipped_when_no_parents(self):
        plan = map_extraction_to_plan(self.BASE)
        assert "sibling_0" not in plan["persons"]
        skip = next(s for s in plan["skipped"] if s["key"] == "sibling_0")
        assert "no parent persons" in skip["reason"]

    def test_siblings_written_when_parents_present(self):
        data = {**self.BASE, "relationships": {**self.BASE["relationships"], "parents": [
            {"given_names": "Andrew", "surname": "Neese", "maiden_name": "", "deceased": True},
            {"given_names": "Nellie", "surname": "Neese", "maiden_name": "Walker", "deceased": True},
        ]}}
        plan = map_extraction_to_plan(data)
        assert "sibling_0" in plan["persons"]
        cpr = next(c for c in plan["relationships"]["child_and_parents"] if c["child"] == "sibling_0")
        assert cpr["parent1"] == "parent_0"
        assert cpr["parent2"] == "parent_1"

    def test_siblings_skipped_when_parents_present_but_all_living_and_excluded(self):
        data = {**self.BASE, "relationships": {**self.BASE["relationships"], "parents": [
            {"given_names": "Andrew", "surname": "Neese", "maiden_name": "", "deceased": False},
        ]}}
        plan = map_extraction_to_plan(data)
        assert "sibling_0" not in plan["persons"]
        assert "parent_0" not in plan["persons"]


class TestLivingRelativeExclusion:
    def _data(self, spouse_deceased):
        return {
            "deceased": {"given_names": "Donna Sue", "surname": "Neese", "gender": "Female"},
            "relationships": {
                "spouses": [{"given_names": "Robert", "surname": "Neese", "deceased": spouse_deceased}],
                "parents": [], "children": [], "siblings": [],
            },
            "eulogy_text": "", "service_details": "", "source_url": "", "raw_text": "obituary text",
        }

    def test_deceased_relative_written_by_default(self):
        plan = map_extraction_to_plan(self._data(spouse_deceased=True))
        assert "spouse_0" in plan["persons"]
        assert plan["persons"]["spouse_0"]["living"] is False
        assert {"person1": "subject", "person2": "spouse_0"} in plan["relationships"]["couples"]

    def test_living_relative_excluded_by_default(self):
        plan = map_extraction_to_plan(self._data(spouse_deceased=False))
        assert "spouse_0" not in plan["persons"]
        skip = next(s for s in plan["skipped"] if s["key"] == "spouse_0")
        assert skip["reason"] == "living, not opted in"

    def test_living_relative_included_when_opted_in(self):
        plan = map_extraction_to_plan(self._data(spouse_deceased=False), include_living=frozenset({"spouse_0"}))
        assert "spouse_0" in plan["persons"]
        assert plan["persons"]["spouse_0"]["living"] is True


class TestChildrenDoNotGuessOtherParent:
    def test_child_cpr_has_subject_as_sole_parent(self):
        data = {
            "deceased": {"given_names": "Donna Sue", "surname": "Neese", "gender": "Female"},
            "relationships": {
                "spouses": [], "parents": [],
                "children": [{"given_names": "Mary Ellen", "surname": "Thompson", "deceased": False}],
                "siblings": [],
            },
            "eulogy_text": "", "service_details": "", "source_url": "", "raw_text": "obit",
        }
        plan = map_extraction_to_plan(data, include_living=frozenset({"child_0"}))
        cpr = next(c for c in plan["relationships"]["child_and_parents"] if c["child"] == "child_0")
        assert cpr["parent1"] == "subject"
        assert cpr["parent2"] is None


class TestBuildSource:
    def test_source_with_url(self):
        deceased = {"given_names": "Donna Sue", "surname": "Neese", "birth_date": "1939-07-25", "death_date": "2025-12-10"}
        source = build_source(deceased, "https://www.example-obits.com/donna-neese", "Donna Sue Neese, age 86...")
        assert source["about"] == "https://www.example-obits.com/donna-neese"
        assert "1939" in source["titles"][0]["value"]
        assert "2025" in source["titles"][0]["value"]
        assert source["notes"][0]["text"] == "Donna Sue Neese, age 86..."

    def test_source_without_url_uses_provenance(self):
        deceased = {"given_names": "Donna Sue", "surname": "Neese"}
        source = build_source(deceased, "", "raw text here", provenance="Daviess County Gazette, print edition")
        assert "about" not in source
        assert "Daviess County Gazette" in source["citations"][0]["value"]

    def test_note_truncated_when_over_limit(self):
        deceased = {"given_names": "X", "surname": "Y"}
        long_text = "a" * 20000
        source = build_source(deceased, "", long_text)
        assert len(source["notes"][0]["text"]) < len(long_text)
        assert "truncated" in source["notes"][0]["text"]
