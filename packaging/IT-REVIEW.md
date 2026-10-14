# IT review: local labor market screen

## Execution and installation

The Windows release is a zipped PyInstaller one-folder console app. Extract it
into a user-owned folder and run `nopoach-screen.exe` beside `_internal`.
No administrator rights, system service, scheduled task, registry installation
or separate Python installation is needed. Python 3.12 source installation with
uv is also supported. Builds are unsigned; no certificate purchase is implied.

The app listens on IPv4 loopback **127.0.0.1:8765**. It opens the default browser
with a one-use token, exchanges it for an HttpOnly, SameSite session cookie,
and checks the request host and write origin. Tokens are per launch. Static
assets, fonts and htmx are local. No CDN, analytics, telemetry, crash upload,
cloud analysis or automatic update service is configured. HTTP access logs are
disabled. Browser history can retain the initial local token URL.

The console stays open during use. Finish analysis and export operations, then
press Ctrl+C in that console; the server waits for its work executor to finish.
Closing a browser tab does not stop the server. The CI smoke test verifies the
frozen app serves the authenticated home and assets, then shuts down cleanly.

## Network inventory

The default desktop launcher configures **no collectors**, so it analyzes local
workspace data. The single shared fetch client permits only unauthenticated
GET/HEAD, forbids account credentials and LinkedIn hosts, checks redirects
against the same allowlist, uses HTTPS for public sources, and records source
receipts. It does not use environment proxies or persistent session cookies.
Its offline setting refuses every retrieval. This is an application policy,
not an operating-system firewall; IT can independently deny outbound traffic.

| Host | Configuration and purpose |
| --- | --- |
| `127.0.0.1` | Local browser/server only; HTTP port 8765. `localhost:8765` is an accepted Host header; the listener remains IPv4 loopback. |
| `www.sec.gov`, `data.sec.gov`, `sec.gov` | Default FetchClient allowlist, public filings and indexes; shared ceiling of 10 requests/second and declared contact User-Agent. Not automatically called by the desktop launcher. |
| `www.bls.gov`, `bls.gov`, `download.bls.gov`, `data.bls.gov` | Optional BLS public bulk context feeds, only when explicitly configured by an analyst. |
| `www.dol.gov`, `dol.gov` | Optional DOL public bulk disclosures, only when explicitly configured by an analyst. |
| IRS bulk-return host chosen by the agency | Form 990 parsers exist, but no IRS hostname is enabled by default. Vet the actual download host and its terms before configuring FetchClient. |

FINRA BrokerCheck is regulator-extract import only; no online retrieval is
implemented or authorized. Franchise disclosure documents are imports. The
catalog links to other data suppliers, but these entries do not enable network
access or authorize purchases. Review any host change before deployment and
record terms/access authority. Requests to public hosts expose the requested
URLs and declared source contact; review those requests under agency policy.

## Writes and data classes

Application code and bundled resources are read from the extracted folder.
The user chooses a workspace outside the source/install tree. The application
creates `workspace.duckdb`, a `parquet/` directory and `provenance.jsonl` there;
DuckDB can also create a write-ahead log and temporary files during work. Raw
source receipts, imported records and analysis results belong to that workspace.
Word dossier exports are written to the workspace's `exports/` directory and
then downloaded by the browser, whose download folder is governed separately.
The Python runtime and numerical libraries may use user/OS temporary and cache
folders; include these in endpoint review. The app does not implement encryption
at rest or an agency identity/access-management system.

A workspace may hold public filings, person-level employment histories,
**state unemployment-insurance wage records**, administrative records,
**investigative productions**, licensed imports, identity-resolution mappings,
source URLs and raw receipts, live employer targets, analysis and pilot results,
and exported dossiers. Treat these as agency records with the highest applicable
confidentiality and retention requirements. **Place workspaces, exports, backups,
OS temporary storage and browser downloads on encrypted, access-controlled
storage under agency policy.** Restrict machine and folder access, apply the
relevant data-sharing agreements, and use approved retention and backup controls.
Nothing in MIT licensing or source publication permits disclosure of these data.

## Review and allowlist by hash

1. Obtain the zip, `SHA256SUMS.txt`, `bom.cdx.json`, this note and the GitHub
   build attestation from the reviewed release. Verify the attestation against
   the actual publishing repository (`gh attestation verify nopoach-screen.zip
   --repo OWNER/REPOSITORY`) and trusted tag/commit before approving it.
2. Verify the zip and SBOM SHA-256 values against the manifest. Extract into
   a review folder; the manifest also covers each relative extracted path.
   For a specific binary use PowerShell `Get-FileHash PATH -Algorithm SHA256`.
3. Review the CycloneDX SBOM and bundled `THIRD-PARTY.md`, `DEPENDENCIES.txt`
   and `licenses/`. The SBOM inventories the build environment, including tools
   not necessarily frozen into the app. The zip retains the vendored font and
   htmx notices. PyInstaller's bootloader exception permits this distribution.
4. Allowlist the reviewed executable and required DLLs by their individual
   SHA-256 values using the agency's endpoint controls. Preserve the entire
   extracted folder; a zip hash alone is not an executable allowlist rule.
   Re-review every new release because hashes change.

Release CI builds on `windows-latest`, verifies the built app on loopback,
checks every manifest hash, emits CycloneDX 1.6 JSON, and uses
`actions/attest-build-provenance`. CI and release dependency installs use
`uv sync --locked --group dev --group build` with the committed `uv.lock`;
an inconsistent lockfile fails the build. Release checkout does not persist
the repository write credential in Git configuration. Every third-party
action is pinned to a full commit SHA, with its release tag in a comment.
Repository hygiene CI always rejects prohibited data paths without logging matches.
The confidential-term denylist and encrypted Actions secret `NOPOACH_DENYLIST` are optional for adopters who want to keep their own confidential terms out of a fork.
Without a denylist, term matching is skipped; a supplied path must name a readable, nonempty file outside the repository or the scan refuses.
It recursively inspects gzip, zip
and tar members, including nested archives and their member names. Per Git blob,
inspection permits at most five nesting levels, 32 MiB of cumulative bytes
(containers and expanded contents), and 1,000 archive members. Unreadable or
over-limit data fails closed; archives are never extracted to disk. Git LFS
pointers require a locally readable object whose SHA-256 and size
match the pointer; that object is inspected with the same limits. The scanner
never fetches LFS objects, so an unavailable historical object blocks release.
Opaque data files fail closed; web font and image assets retain existing term
matching without text-decoding requirements. This note describes the build;
an agency's own IT approval remains its decision.
