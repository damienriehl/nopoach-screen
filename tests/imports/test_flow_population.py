from datetime import date

import pytest

from nopoach.imports.flow_population import import_population, load_population
from nopoach.market.define import FlowDefinedMarket
from nopoach.store.workspace import Workspace


def test_population_import_uses_own_scoped_dated_denominators(tmp_path):
    body = (
        b"employer_id,year,value,lower,upper,source,as_of\n"
        b"a,2020,10,9,11,Synthetic count,2020-01-01\n"
    )
    metadata = {
        "population_id": "synthetic",
        "description": "Synthetic population",
        "provenance_ids": ["workers"],
        "employer_ids": ["a", "b"],
        "source": "Synthetic declaration",
    }
    with Workspace.open(tmp_path / "workspace") as workspace:
        ref = import_population(workspace, body, metadata)
        population = load_population(workspace, ref)
        scope = FlowDefinedMarket(
            2020,
            2020,
            "synthetic",
            "Synthetic population",
            frozenset({"workers"}),
            frozenset({"a", "b"}),
            frozenset({"a", "b"}),
        )
        estimates = population.estimates(scope, as_of=date(2020, 12, 31))
        assert estimates[0].headcount.value == 10
        assert population.estimates(scope, as_of=date(2019, 12, 31)) == ()
        assert ref == import_population(workspace, body, metadata)
        with pytest.raises(ValueError, match="conflict"):
            import_population(
                workspace,
                body + b"a,2020,20,20,20,Synthetic count,2020-01-01\n",
                metadata,
            )
