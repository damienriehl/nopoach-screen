# Third-party software and data sources

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

## Other data and reference sources

| Source | License or terms / permitted route |
| --- | --- |
| BLS OEWS and QCEW | Public government statistical tables; reuse policy: https://www.bls.gov/bls/linksite.htm. Context and denominators, not individual moves; no collection enabled by the desktop launcher. |
| DOL OFLC LCA and PERM | Public bulk disclosure files; https://www.dol.gov/general/aboutdol/copyright. LCA job offers and PERM records are occupation/employer context, not a complete employment history. |
| Franchise disclosure documents | Agency-supplied lawful imports; rights and terms depend on the originating filing repository. No universal redistribution license asserted. |
| Agency CSV/Parquet, UI wage records, investigative productions | Agency authority, data-sharing agreements and source-specific restrictions govern; these are never distributed with the code. |
| Census LEHD restricted microdata | Title 13 and approved research terms; method validation only, never employer-identifying enforcement leads. Catalog reference only. |
| LinkedIn Talent Insights / Economic Graph | First-party licensed aggregate products; verify original-source terms. No profile scraping, account-based collector or access purchase is implemented. Catalog reference only. |
| Revelio Labs, Coresignal, People Data Labs, Live Data Technologies, Lightcast, Bright Data, Aura | Commercial provider terms and original collection rights require review; provenance can be unclear or possibly unlawful. Listing is not permission, a verified price, or a redistribution license. Catalog references only. |
| Equifax The Work Number | Employment-verification / FCRA permissible-purpose restrictions; not recommended or used for enforcement lead generation. Catalog reference only. |
| Public case references | Citations in `src/nopoach/backtest/cases.yaml` describe published historical cases. No historical person-level datasets or pilot results are bundled. |
| Synthetic fixtures | Generated project fixtures under the project's MIT license; they are not collected records. |

The catalog is a dated, unverified terms/cost snapshot. New collection requires
review of the actual host's current terms. No spending, login, account use or
terms workaround is authorized. Drop a source if its terms prohibit the use.

## Vendored resources and distribution tools

| Component | License and notice |
| --- | --- |
| Python 3.12 runtime | PSF-2.0 and incorporated notices; release includes Python's license when supplied by the interpreter distribution. |
| htmx | BSD-2-Clause; `src/nopoach/app/static/htmx-LICENSE.txt` is shipped beside the vendored script. |
| Atkinson Hyperlegible Next | SIL Open Font License 1.1 (OFL-1.1); bundled `OFL-AtkinsonHyperlegibleNext.txt`. |
| Source Serif 4, regular and italic | SIL Open Font License 1.1 (OFL-1.1); bundled `OFL-SourceSerif4.txt`. |
| PyInstaller 6.22.3 | GPL-2.0 with the PyInstaller bootloader exception allowing application bundling under its own license; preserve installed COPYING/exception notices. |
| CycloneDX BOM 7.5.0 | Apache-2.0. Generates the CycloneDX 1.6 environment SBOM. |
| uv | MIT OR Apache-2.0; installation/build tool, not bundled as an application runtime. |

## Python dependency inventory

The following inventory records the locally supplied Python 3.12 runtime,
development and build dependency closure at implementation time. Licenses come
from installed distribution metadata, not a live legal review. Build releases
also include versioned `DEPENDENCIES.txt`, available installed license texts
under `licenses/`, and a CycloneDX SBOM. Those release inventories are authoritative
for resolved versions and may include development/build tools not embedded in
the executable. Native library notices included in wheel license directories
are preserved, including numerical-library and font-library notices. Windows
resolution may add platform-specific packages; their installed notices and
SBOM entries are collected by the same build step. Keep this inventory current
when dependencies change. The application itself remains MIT licensed.


| Distribution | Snapshot version | Declared license |
| --- | --- | --- |
| altgraph | 0.17.5 | MIT License |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| arrow | 1.4.0 | Apache Software License |
| ast_serialize | 0.13.0 | MIT |
| asttokens | 3.0.2 | Apache 2.0 |
| attrs | 26.1.0 | MIT |
| axe-playwright-python | 0.1.8 | MIT (installed LICENSE); embedded axe-core uses MPL-2.0 |
| babel | 2.18.0 | BSD License |
| boolean.py | 5.0 | BSD-2-Clause |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| chardet | 7.6.0 | 0BSD |
| click | 8.5.0 | BSD-3-Clause |
| cloudpickle | 3.1.2 | BSD License |
| contourpy | 1.4.0 | BSD-3-Clause |
| cycler | 0.12.1 | BSD License |
| cyclonedx-bom | 7.5.0 | Apache Software License |
| cyclonedx-python-lib | 11.12.0 | Apache Software License |
| defusedxml | 0.7.1 | Python Software Foundation License |
| duckdb | 1.5.6 | MIT License |
| executing | 2.3.0 | MIT License |
| faicons | 0.2.2 | MIT License |
| fastapi | 0.143.0 | MIT |
| fonttools | 4.66.1 | MIT |
| formulaic | 1.2.2 | MIT |
| fqdn | 1.6.0 | MPL-2.0 |
| great-tables | 1.0.0 | MIT License |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT License |
| htmltools | 0.7.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| idna | 3.20 | BSD-3-Clause |
| importlib_metadata | 9.0.1 | Apache-2.0 |
| importlib_resources | 7.1.0 | Apache-2.0 |
| iniconfig | 2.3.1 | MIT |
| interface_meta | 2.0.1 | MIT |
| ipython | 9.17.1 | BSD-3-Clause |
| ipython_pygments_lexers | 1.1.1 | BSD License |
| isoduration | 20.11.0 | ISC License (ISCL) |
| jedi | 0.20.1 | MIT License |
| Jinja2 | 3.1.6 | BSD License |
| joblib | 1.6.0 | BSD-3-Clause |
| jsonpointer | 3.2.1 | BSD License |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| kiwisolver | 1.5.1 | BSD License |
| lark | 1.3.1 | MIT License |
| librt | 0.16.0 | MIT |
| license-expression | 30.4.4 | Apache-2.0 |
| lxml | 6.1.3 | BSD-3-Clause |
| maketables | 0.1.9 | MIT License |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| matplotlib | 3.11.2 | Python Software Foundation License |
| matplotlib-inline | 0.2.2 | BSD-3-Clause |
| multimark | 0.4.0 | MIT AND BSD-2-Clause |
| mypy | 2.4.0 | MIT |
| mypy_extensions | 1.1.0 | MIT |
| narwhals | 2.27.1 | MIT |
| nokap | 0.1.0 | MIT License |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packageurl-python | 0.17.6 | MIT License |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.6 | BSD License |
| parso | 0.8.7 | MIT License |
| pathspec | 1.1.1 | Mozilla Public License 2.0 (MPL 2.0) |
| patsy | 1.0.3 | BSD License |
| pexpect | 4.9.0 | ISC License (ISCL) |
| pillow | 12.3.0 | MIT-CMU |
| pip-requirements-parser | 32.0.1 | MIT |
| playwright | 1.63.0 | Apache-2.0 |
| pluggy | 1.6.0 | MIT License |
| prompt_toolkit | 3.0.53 | BSD License |
| psutil | 7.2.2 | BSD-3-Clause |
| ptyprocess | 0.7.0 | ISC License (ISCL) |
| pure_eval | 0.2.4 | MIT License |
| py-serializable | 2.1.0 | Apache Software License |
| pyarrow | 26.0.0 | Apache-2.0 |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| pyee | 13.0.1 | MIT License |
| pyfixest | 0.60.0 | MIT License |
| Pygments | 2.21.0 | BSD-2-Clause |
| pyinstaller | 6.22.3 | GPL-2.0 with bootloader exception |
| pyinstaller-hooks-contrib | 2026.8 | Apache Software License, GNU General Public License v2 (GPLv2) |
| pyparsing | 3.3.3 | MIT |
| pypdf | 6.20.1 | BSD-3-Clause |
| pytest | 9.1.1 | MIT |
| python-dateutil | 2.9.0.post0 | BSD License, Apache Software License |
| python-docx | 1.2.0 | MIT License |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT License |
| RapidFuzz | 3.14.6 | MIT |
| referencing | 0.37.0 | MIT |
| rfc3339-validator | 0.1.4 | MIT License |
| rfc3986-validator | 0.1.1 | MIT License |
| rfc3987-syntax | 1.1.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| ruff | 0.17.0 | MIT |
| scipy | 1.18.1 | BSD License |
| seaborn | 0.13.2 | BSD License |
| setuptools | 84.0.0 | MIT |
| six | 1.17.0 | MIT License |
| sortedcontainers | 2.4.0 | Apache Software License |
| splink | 5.0.0 | MIT |
| sqlglot | 30.22.0 | MIT |
| stack-data | 0.6.3 | MIT License |
| starlette | 1.8.0 | BSD-3-Clause |
| statsmodels | 0.15.0 | BSD-3-Clause |
| tabulate | 0.10.0 | MIT |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| traitlets | 5.16.1 | BSD License |
| types-PyYAML | 6.0.12.20260906 | Apache-2.0 |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.5 | Apache-2.0 |
| uri-template | 1.3.0 | MIT License |
| uvicorn | 0.54.0 | BSD-3-Clause |
| wcwidth | 0.9.2 | MIT License |
| webcolors | 25.10.0 | BSD License |
| websockets | 17.2 | BSD-3-Clause |
| wrapt | 2.5.0 | BSD-2-Clause |
| zipp | 4.1.1 | MIT |
