from datetime import date

import pytest

from nopoach.resolve.employers import EmployerResolver
from nopoach.resolve.parents import ParentResolver
from nopoach.store.workspace import Workspace


def receipt(workspace):
    return workspace.provenance.append(
        source="synthetic",
        url="file:fixture",
        method="IMPORT",
        body=b"synthetic",
        collection_method="agency_import",
    ).id


def test_names_ids_and_parent_as_of(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        provenance = receipt(workspace)
        resolver = EmployerResolver(workspace)
        pixar = resolver.resolve(
            "Pixar Animation Studios",
            provenance_id=provenance,
            employer_id="000100",
            employer_id_type="cik",
        )
        alias = resolver.resolve("PIXAR", provenance_id=provenance)
        assert alias.entity_id == pixar.entity_id
        assert alias.evidence["engine"] == "splink-5-duckdb"
        assert alias.evidence["match_probability"] > 0.99
        disney = resolver.resolve("Disney", provenance_id=provenance)
        parents = ParentResolver(workspace)
        parents.add_link(
            pixar.entity_id,
            disney.entity_id,
            valid_from=date(2006, 5, 5),
            provenance_id=provenance,
            origin="sec_ex21",
        )
        assert (
            parents.ultimate_parent(pixar.entity_id, date(2010, 1, 1))
            == disney.entity_id
        )
        assert (
            parents.ultimate_parent(pixar.entity_id, date(2005, 1, 1))
            == pixar.entity_id
        )
        assert (
            parents.evidence(pixar.entity_id, date(2010, 1, 1))[0]["provenance_id"]
            == provenance
        )
        same_id = resolver.resolve(
            "Different Filed Name",
            provenance_id=provenance,
            employer_id="100",
            employer_id_type="cik",
        )
        assert same_id.entity_id == pixar.entity_id
        different = resolver.resolve(
            "PIXAR", provenance_id=provenance, employer_id="200", employer_id_type="cik"
        )
        assert different.entity_id != pixar.entity_id
        for id_type in ("crd", "ein"):
            entity = resolver.resolve(
                f"Synthetic {id_type}",
                provenance_id=provenance,
                employer_id="123456789",
                employer_id_type=id_type,
            )
            assert (
                resolver.resolve(
                    "Synthetic Alias",
                    provenance_id=provenance,
                    employer_id="123456789",
                    employer_id_type=id_type,
                ).entity_id
                == entity.entity_id
            )


def test_manual_parent_override_chain_and_end_date(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        provenance = receipt(workspace)
        employers = EmployerResolver(workspace)
        child, parent, top = [
            employers.resolve(name, provenance_id=provenance).entity_id
            for name in ("Synthetic Child", "Synthetic Parent", "Synthetic Top")
        ]
        resolver = ParentResolver(workspace)
        resolver.add_link(
            child,
            parent,
            valid_from=date(2000, 1, 1),
            provenance_id=provenance,
            origin="sec_ex21",
        )
        resolver.add_link(
            parent,
            top,
            valid_from=date(2000, 1, 1),
            provenance_id=provenance,
            origin="sec_ex21",
        )
        assert resolver.ultimate_parent(child, date(2010, 1, 1)) == top
        resolver.add_link(
            child,
            top,
            valid_from=date(2011, 1, 1),
            valid_to=date(2012, 12, 31),
            provenance_id=provenance,
            origin="manual_override",
            reason="Synthetic lawyer correction",
        )
        assert (
            resolver.evidence(child, date(2012, 1, 1))[0]["origin"] == "manual_override"
        )
        assert resolver.evidence(child, date(2013, 1, 1))[0]["parent_id"] == parent
        with pytest.raises(ValueError, match="cycle"):
            resolver.add_link(
                top,
                child,
                valid_from=date(2010, 1, 1),
                provenance_id=provenance,
                origin="manual_override",
                reason="cycle",
            )


def test_existing_u1_employer_id_remains_authoritative_after_alias(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        provenance = receipt(workspace)
        workspace.connection.execute(
            "INSERT INTO employers VALUES ('synthetic-existing', "
            "'Synthetic Existing', '100', ?)",
            [provenance],
        )
        resolver = EmployerResolver(workspace)
        assert (
            resolver.resolve("Synthetic Existing", provenance_id=provenance).entity_id
            == "synthetic-existing"
        )
        conflicting = resolver.resolve(
            "Synthetic Existing",
            provenance_id=provenance,
            employer_id="200",
            employer_id_type="cik",
        )
        assert conflicting.entity_id != "synthetic-existing"
        assert resolver.evidence(conflicting.entity_id)[0]["external_id"] == "200"


def test_unicode_names_do_not_merge_distinct_employers(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        provenance = receipt(workspace)
        resolver = EmployerResolver(workspace)
        first = resolver.resolve("Synthetic 北京", provenance_id=provenance)
        second = resolver.resolve("Synthetic 東京", provenance_id=provenance)
        assert first.entity_id != second.entity_id
