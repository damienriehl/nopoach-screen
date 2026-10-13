"""Versioned, offline source catalog and recommendations for U6 flow gaps.

Catalog inclusion is not collection authorization or a guarantee of coverage.
The agency obtains and licenses any records; this module makes no network calls.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from nopoach.imports.schema import ProvenanceClass
from nopoach.market.coverage import CoverageReport
from nopoach.market.sufficiency import SignalSufficiency

CATALOG_PATH = Path(__file__).with_name("sources.yaml")
STALENESS_NOTICE = "price and terms may be out of date"

# Provenance priority wins over occupation fit; file order settles equal fits.
_PRIORITY = {
    ProvenanceClass.GOVERNMENT_ADMINISTRATIVE: 0,
    ProvenanceClass.FIRST_PARTY_LINKEDIN: 1,
    ProvenanceClass.LICENSED_ORIGINAL: 1,
    ProvenanceClass.CONSENT_BASED: 1,
    ProvenanceClass.THIRD_PARTY_UNCLEAR: 2,
}
_LABELS = {
    ProvenanceClass.SYNTHETIC: "synthetic",
    ProvenanceClass.GOVERNMENT_ADMINISTRATIVE: "government or administrative records",
    ProvenanceClass.FIRST_PARTY_LINKEDIN: "first-party LinkedIn",
    ProvenanceClass.LICENSED_ORIGINAL: "licensed from the original source",
    ProvenanceClass.CONSENT_BASED: "consent-based",
    ProvenanceClass.THIRD_PARTY_UNCLEAR: (
        "third-party web aggregation with unclear or possibly unlawful collection"
    ),
}
_EXPLANATIONS = {
    ProvenanceClass.SYNTHETIC: (
        "Synthetic data demonstrate the tool and cannot support an enforcement lead."
    ),
    ProvenanceClass.GOVERNMENT_ADMINISTRATIVE: (
        "Government records come from an administering agency, so an enforcement "
        "lead depends on authorized access and the applicable disclosure rules."
    ),
    ProvenanceClass.FIRST_PARTY_LINKEDIN: (
        "LinkedIn supplies its own platform data, so an enforcement lead depends "
        "on the licensed access terms and the limits of aggregate coverage."
    ),
    ProvenanceClass.LICENSED_ORIGINAL: (
        "The provider licenses data from the original source, so an enforcement "
        "lead depends on the license and any statutory use limits."
    ),
    ProvenanceClass.CONSENT_BASED: (
        "People supplied these data with consent, so an enforcement lead depends "
        "on whether that consent covers the agency's use."
    ),
    ProvenanceClass.THIRD_PARTY_UNCLEAR: (
        "Collection and licensing are unclear, so the agency must establish lawful "
        "provenance before using these data for an enforcement lead."
    ),
}


class Source(BaseModel):
    """Maintainer-editable entry using the same provenance values as imports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    provenance_class: ProvenanceClass
    cost: str = Field(min_length=1)
    price_verified: bool
    data_kind: Literal["person_level_histories", "aggregates", "payroll"]
    what_it_adds: str = Field(min_length=1)
    occupations_covered_well: str = Field(min_length=1)
    occupation_prefixes: tuple[str, ...]
    allowed_use: Literal["leads", "method_validation_only", "no_leads"]
    restriction: str
    notes: str
    last_checked: date
    source_link: HttpUrl

    @property
    def provenance_label(self) -> str:
        return _LABELS[self.provenance_class]

    @property
    def provenance_explanation(self) -> str:
        return _EXPLANATIONS[self.provenance_class]

    @property
    def cost_label(self) -> str:
        if self.price_verified or self.cost == "quote required":
            return self.cost
        return f"{self.cost} (unverified)"

    def staleness_notice(self, as_of: date) -> str | None:
        return STALENESS_NOTICE if (as_of - self.last_checked).days > 180 else None


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    sources: tuple[Source, ...]


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    """Load and validate the shipped snapshot or a maintainer's replacement."""
    return Catalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _render_source(source: Source, staleness_notice: str | None) -> str:
    lines = [
        source.name,
        f"Cost: {source.cost_label}",
        f"Provenance: {source.provenance_label}",
        source.provenance_explanation,
        f"Adds: {source.what_it_adds}",
        f"Occupation coverage: {source.occupations_covered_well}",
        f"Allowed use: {source.allowed_use}",
    ]
    if source.restriction:
        lines.append(f"Restriction: {source.restriction}")
    lines.extend(
        [
            source.notes,
            f"Last checked: {source.last_checked.isoformat()}",
            f"Source: {source.source_link}",
        ]
    )
    if staleness_notice:
        lines.append(staleness_notice)
    return "\n".join(lines)


def render_catalog(catalog: Catalog, *, as_of: date | None = None) -> str:
    """Plain-text listing that retains restricted entries and their warnings."""
    today = as_of if as_of is not None else date.today()
    return "\n\n".join(
        _render_source(source, source.staleness_notice(today))
        for source in catalog.sources
    )


@dataclass(frozen=True)
class Recommendation:
    source: Source
    market_addition: str
    staleness_notice: str | None

    @property
    def cost(self) -> str:
        return self.source.cost_label

    @property
    def provenance_label(self) -> str:
        return self.source.provenance_label

    @property
    def provenance_explanation(self) -> str:
        return self.source.provenance_explanation

    def render(self) -> str:
        return (
            _render_source(self.source, self.staleness_notice)
            + "\nFor this market: "
            + self.market_addition
        )


def _occupation_fit(source: Source, report: CoverageReport) -> int:
    if not source.occupation_prefixes:
        return 0  # Broad coverage without a professional-profile preference.
    matches = sum(
        any(code.startswith(prefix) for prefix in source.occupation_prefixes)
        for code in report.market.occupation_codes
    )
    return matches if matches else -1


def recommend_sources(
    report: CoverageReport,
    sufficiency: SignalSufficiency,
    *,
    catalog: Catalog | None = None,
    as_of: date | None = None,
) -> list[Recommendation]:
    """Recommend flow coverage inputs, not remedies for clause/tie feed gaps.

    All eligible entries remain visible even with uncertain occupation fit;
    occupation preferences order entries only within their provenance tier.
    Aggregate products supply context rather than individual worker moves.
    """
    if sufficiency.flow.status == "sufficient":
        return []
    catalog = catalog if catalog is not None else load_catalog()
    today = as_of if as_of is not None else date.today()
    eligible = [
        source
        for source in catalog.sources
        if source.allowed_use == "leads"
        and source.provenance_class != ProvenanceClass.SYNTHETIC
    ]
    eligible.sort(
        key=lambda source: (
            _PRIORITY[source.provenance_class],
            -_occupation_fit(source, report),
        )
    )
    market = report.market
    context = (
        f"Occupations {', '.join(sorted(market.occupation_codes))}; "
        f"{market.geography.kind} {market.geography.code}; "
        f"{market.start_year}-{market.end_year}. "
        f"Coverage gap: {sufficiency.flow.reason} "
    )
    return [
        Recommendation(
            source,
            context
            + source.what_it_adds
            + (
                " Occupation-specific coverage must be confirmed before purchase "
                "or request."
                if _occupation_fit(source, report) < 0
                else " Confirm coverage for these occupations, geography and years."
            ),
            source.staleness_notice(today),
        )
        for source in eligible
    ]
