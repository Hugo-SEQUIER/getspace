# Third-party components

License inventory for the Space repository, regenerated on 9 October 2026.

Space itself is free software under the GNU Affero General Public License, version 3. See [LICENSE](LICENSE) for the terms and [NOTICE](NOTICE) for the copyright statement. This file records the licenses of the components that Space ships or depends on directly.

Transitive dependencies are not listed; each package remains under its own license. A distribution should regenerate this inventory, for example with `pip-licenses` for the backend and `license-checker` for the website.

## Vendored component

| Component | Version | License | Notice |
|---|---|---|---|
| Memora text inference runtime | pinned commit `dec3f8f2444eace7004fc084abe1be9f3d88270e` | MIT | Text retained in `backend/src/space/_vendor/memora/LICENSE`, which is byte-identical to the [upstream license](https://github.com/microsoft/Memora). Space-specific modifications are recorded in `backend/src/space/_vendor/memora/README.md`. |

The MIT License permits commercial use, modification and distribution. It requires the copyright notice and the permission notice to stay with the software, and it grants no trademark or patent rights. The vendored runtime stays under MIT and is not relicensed under the AGPL.

## Backend dependencies

Direct runtime dependencies from `backend/pyproject.toml`, at the versions locked in `backend/uv.lock`, with licenses read from the installed package metadata.

| Package | Locked version | License |
|---|---|---|
| alembic | 1.19.2 | MIT |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| fastapi | 0.141.1 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| mcp | 1.30.0 | MIT |
| psycopg, with the `binary` extra (psycopg-binary) | 3.3.5 | LGPL-3.0-only |
| psycopg-pool | 3.3.1 | LGPL-3.0-only |
| pydantic-settings | 2.15.0 | MIT |
| rank-bm25 | 0.2.2 | Apache-2.0 |
| sqlalchemy | 2.0.52 | MIT |
| uvicorn | 0.52.4 | BSD-3-Clause |

Development dependencies, used only by the tests and checks: `pyjwt` 2.13.0 with the `crypto` extra (MIT), `pytest` 9.1.1 (MIT) and `ruff` 0.16.7 (MIT). `boto3` and `pyjwt` are no longer runtime dependencies.

`psycopg`, `psycopg-binary` and `psycopg-pool` are LGPL-3.0-only. LGPL obligations apply to a distributed installation: keep the library replaceable and supply its source and license. The `psycopg[binary]` extra also bundles `libpq`, which is covered by the PostgreSQL License.

The compose stack also runs third-party container images, `pgvector/pgvector` and `caddy`, builds the backend from the `python` and `uv` images and the website from the `node` image; each image is under its own licenses.

## Website dependencies

Direct runtime dependencies from `apps/web/package.json`, at the versions locked in `apps/web/package-lock.json`, with the licenses the lockfile records.

| Package | Locked version | License |
|---|---|---|
| @dnd-kit/core | 6.3.1 | MIT |
| @dnd-kit/utilities | 3.2.2 | MIT |
| @hono/node-server | 2.1.3 | MIT |
| @tanstack/query-core | 5.104.0 | MIT |
| @tanstack/react-query | 5.104.0 | MIT |
| @tanstack/react-router | 1.170.41 | MIT |
| @tanstack/react-start | 1.168.60 | MIT |
| d3-force | 3.0.0 | ISC |
| effect | 4.0.0 | MIT |
| fflate | 0.8.3 | MIT |
| idb | 8.0.3 | ISC |
| mammoth | 1.12.3 | BSD-2-Clause |
| papaparse | 5.7.0 | MIT |
| pdfjs-dist | 6.3.289 | Apache-2.0 |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| read-excel-file | 9.3.10 | MIT |
| zod | 4.6.5 | MIT |

The website's container image also ships `hono` 4.13.12 (MIT), the peer dependency of `@hono/node-server`.

Development dependencies: `@e2e-dev/web` (Apache-2.0), `@types/d3-force`, `@types/node`, `@types/papaparse`, `@types/react` and `@types/react-dom` (MIT), `@vitejs/plugin-react` (MIT), `ai` (Apache-2.0), `e2e` (Apache-2.0), `fake-indexeddb` (Apache-2.0), `typescript` (Apache-2.0) and `vite` (MIT).

`apps/web/public/vendor/pdfjs/` is generated at build time from the installed PDF.js package, together with its license file, and is not tracked. The PDF.js license is Apache-2.0.

## Assets

The website's brand and design assets were produced for this project. They are covered by [LICENSE](LICENSE) unless a file states otherwise.

Ecosystem marks under `apps/web/public/integrations/` belong to their respective owners and are used to identify integration targets. The per-file attributions, sources and retained license texts are documented in [apps/web/public/integrations/README.md](apps/web/public/integrations/README.md). Those marks do not indicate partnerships or verified compatibility.

No font files are tracked in this repository.
