from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from nopoach.catalog.recommend import CATALOG_PATH, load_catalog, render_catalog
from nopoach.imports.schema import ProvenanceClass


def test_seed_catalog_has_version_and_all_requested_sources():
    catalog = load_catalog()
    assert catalog.version == 1
    assert {source.id for source in catalog.sources} == {
        "state-ui-wage-records",
        "census-lehd",
        "linkedin",
        "revelio",
        "coresignal",
        "people-data-labs",
        "live-data-technologies",
        "lightcast",
        "bright-data",
        "aura",
        "work-number",
    }
    for source in catalog.sources:
        assert isinstance(source.provenance_class, ProvenanceClass)
        assert source.cost
        assert source.what_it_adds
        assert source.occupations_covered_well
        assert source.last_checked == date(2026, 10, 13)
        assert str(source.source_link).startswith("https://")
    sources = {source.id: source for source in catalog.sources}
    assert sources["state-ui-wage-records"].provenance_class == (
        ProvenanceClass.GOVERNMENT_ADMINISTRATIVE
    )
    assert sources["linkedin"].provenance_class == ProvenanceClass.FIRST_PARTY_LINKEDIN
    assert sources["work-number"].provenance_class == ProvenanceClass.LICENSED_ORIGINAL
    for source_id in (
        "revelio",
        "coresignal",
        "people-data-labs",
        "live-data-technologies",
        "lightcast",
        "bright-data",
        "aura",
    ):
        assert (
            sources[source_id].provenance_class == ProvenanceClass.THIRD_PARTY_UNCLEAR
        )
    assert "$15,000-20,000" in sources["census-lehd"].cost
    assert "$1,250" in sources["revelio"].cost
    assert "$1,500" in sources["coresignal"].cost
    assert all(
        not sources[source_id].price_verified
        for source_id in ("census-lehd", "revelio", "coresignal")
    )
    assert "WRDS" in sources["revelio"].notes
    assert "LinkedIn" in sources["aura"].notes
    assert sources["linkedin"].data_kind == "aggregates"
    assert "no person-level" in sources["linkedin"].what_it_adds


def test_missing_provenance_class_fails_catalog_schema(tmp_path):
    data = yaml.safe_load(CATALOG_PATH.read_text())
    del data["sources"][0]["provenance_class"]
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="provenance_class"):
        load_catalog(path)


def test_restricted_sources_remain_visible_in_catalog_with_restrictions():
    catalog = load_catalog()
    sources = {source.id: source for source in catalog.sources}
    assert sources["census-lehd"].allowed_use == "method_validation_only"
    assert "Title 13" in sources["census-lehd"].restriction
    assert "identifies an employer" in sources["census-lehd"].restriction
    assert sources["work-number"].allowed_use == "no_leads"
    assert "FCRA" in sources["work-number"].restriction
    rendered = render_catalog(catalog, as_of=date(2026, 10, 13))
    assert "Title 13" in rendered
    assert "FCRA" in rendered
    assert "unverified" in rendered
    assert "20 CFR 603" in rendered
    assert "state law" in rendered
