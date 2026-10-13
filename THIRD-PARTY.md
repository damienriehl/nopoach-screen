# U4 person-level data sources

| Source | Access/use basis | Implementation |
| --- | --- | --- |
| SEC EDGAR | Public filings; [SEC access/reuse FAQ](https://www.sec.gov/files/about/webmaster-faq.htm). U1 enforces a declared User-Agent and at most ten requests per second across SEC hosts. | Full daily form indexes, then relevant Forms 3/4/5, 8-K Item 5.02, and DEF 14A for the full market and parents. Source receipts remain linked in imported rows. |
| IRS Form 990 | Public exempt-organization returns distributed by the IRS through [Form 990 series downloads](https://www.irs.gov/charities-non-profits/form-990-series-downloads). | Full bulk CSV/JSON e-file indexes, then nonprofit market/parent returns. Hosts must be vetted and configured on U1's shared fetch client. Part VII roles are tax-year observations. |
| FINRA BrokerCheck | **Current online terms not reviewed:** this implementation runs offline. No permission for automated retrieval or online lead-generation use is asserted. | **Regulator-extract import only.** The agency supplies data it lawfully holds through regulatory channels. Imports retain the raw-file hash, declared source, collection date, and agency-import method. Coverage metadata explicitly reports import-only access. |

TODO(host_review_brokercheck_terms): The host must read and record the current
[FINRA terms](https://www.finra.org/terms-of-use), BrokerCheck's applicable site
terms, and their permitted-use basis before deciding whether an automated
collector is allowed. If terms forbid the planned use, drop online retrieval
and record why; do not log in, use an account, or work around restrictions.

All committed person-level fixtures are synthetic. The SEC fixture README
records the offline-host exception and the outstanding need for a lawful,
accession-cited public-record recording under KTD15. No collected records,
real-person histories, agency identity, or credentials are included.

This document records U4's sources only; dependency-license inventory and other
units' sources remain the owning units' work.
