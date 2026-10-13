"""Import lawfully held regulator extracts; never retrieve BrokerCheck online.

TODO(host_review_brokercheck_terms): Read the CURRENT BrokerCheck terms at
https://www.finra.org/terms-of-use and the BrokerCheck site's applicable terms;
record whether agency lead-generation use and automated retrieval are permitted
in THIRD-PARTY.md before considering an automated collector. Current terms could
not be reviewed in the offline implementation environment. No permitted online
retrieval basis is asserted here, and this module contains no network path.

Permitted-use basis for this adapter: the agency supplies an extract it lawfully
holds via regulatory channels (R13). That declaration remains the agency's
responsibility; a provenance label is not a certification of lawful possession.
CSV/Parquet use U3's documented dates/precision and column mapper. The default
regulator layout is demonstrated only with synthetic records. An individual CRD
identifies a person across firms, and firm CRDs identify the employers. Original
file bytes, import method, source, and collection date are retained by U3.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nopoach.imports.loader import ImportResult, import_file
from nopoach.imports.schema import ProvenanceClass
from nopoach.store.workspace import Workspace

_COLUMNS = {
    "person_key": "individual_crd",
    "employer_name": "firm_name",
    "employer_id": "firm_crd",
    "title": "registration_role",
    "location": "branch_location",
}


def import_regulator_extract(
    workspace: Workspace,
    path: str | Path,
    *,
    source: str = "FINRA regulator-supplied extract",
    column_map: Mapping[str, str] | None = None,
) -> ImportResult:
    """Import a regulator extract via U3; collection permission is not assumed.

    Include employer_id_type=crd in files to retain explicit firm-ID typing.
    Without it U3 uses the source name as the employer ID namespace, avoiding an
    assertion that an untyped provider ID is globally a CRD. Person-key namespace
    is explicitly FINRA individual CRD for this adapter's documented layout.
    """
    return import_file(
        workspace,
        path,
        source=source,
        provenance_class=ProvenanceClass.GOVERNMENT_ADMINISTRATIVE,
        column_map={**_COLUMNS, **(column_map or {})},
        person_key_namespace="finra_individual_crd",
    )


def coverage_report() -> dict[str, Any]:
    """Coverage metadata for the caller's market report (U6 owns the panel)."""
    return {
        "source": "FINRA BrokerCheck",
        "automated_collection": False,
        "retrieval_mode": "regulator_extract_import",
        "terms_review": "pending_host_review",
        "provenance_class": ProvenanceClass.GOVERNMENT_ADMINISTRATIVE.value,
        "reason": "Import only: dated FINRA histories require a lawfully held "
        "regulator-supplied extract. Current online terms await host review.",
    }
