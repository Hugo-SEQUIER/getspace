# End-to-end coverage

What the browser suites check, by surface, and which tier checks it. Keep this table current when a test file is added or a surface changes; a surface with no row is uncovered by design, not by accident.

## Tiers

| Tier | Command | What is real | What it catches |
|---|---|---|---|
| Unit guard | `npm test` (`tests/*.test.mjs`) | The proxy allowlist, the client source, the unlock session and its rate limiter | A backend path the browser calls that the website proxy would refuse; session signing, expiry and unlock limits |
| Mocked e2e | `npm run test:e2e` (`tests/*.e2e.ts`) | The React app on the Vite dev server; `/api` answered in the browser by `tests/support/space-api.ts`, which also fails any request the proxy would refuse | UI behaviour, optimistic updates, retries, failure handling, layout; the proxy gate as a side effect |
| Real stack | `npm run test:e2e:stack` (`tests/stack/*.e2e.ts`) | The production website build, the real `/api` proxy and the real Python backend in key mode, with a disposable TLS PostgreSQL with pgvector. The access key is random per run. | The unlock gate, proxy and backend contract, persistence, attribution, revisions, search, every signed-in route rendering with real data |

## Surfaces

| Surface | Real stack | Mocked e2e | Notes |
|---|---|---|---|
| Unlock gate: wrong key, unlock, lock, other tabs | `auth.setup`, `surfaces` | `access` | Private mode (no gate) in `access` |
| Home: greeting, quick actions, Jump back in | `surfaces`, `workspace` | `attention` | |
| Home: Needs you, New since you looked, badge, mark seen | `attention` | `attention` | Real stack writes as a client through a personal API key |
| First-run tour | `auth.setup` (dismissed) | | Content of the tour not yet asserted |
| Documents: create, list, open | `workspace`, `documents` | | |
| Documents: edit, revisions, history, delete | `documents` | | Folders, sections, drag into folder and upload are not covered |
| Tasks: create, detail, status | `workspace`, `projects` | `tasks` | |
| Tasks: board drag, keyboard moves, columns, bulk actions, view-only | | `tasks` | |
| Tasks board: database rows planned for today | | `today-plan` | |
| Search: titles answer before the index, body matches after one index read, type tabs, the palette reuses the index | `workspace` | `search` | |
| Projects: create, rename, task filter, linked databases | `projects` | `project-databases` | Trash and sections: not yet |
| Databases: create, records, reload, record panel | `databases` | `database-record-panel` | Properties, board view and import are not covered |
| Webhooks: create with signing secret | `databases` | | Rotate, remove and deliveries: not yet |
| Agents: create identity | `agents` | | |
| Agents: connect an agent with a token | | `agent-tokens` | The token is shown once with client setups |
| Agents: connector address for apps that sign in | | `agent-tokens` | Key mode only; absent in private mode |
| Device-code connection | | `connect-code` | Includes unlocking before reviewing the code |
| App sign-in (OAuth): consent, return to the app, refusal, finish by hand | `oauth` | `oauth-consent` | The real stack registers a client, exchanges the code with PKCE and calls MCP with the token |
| Memory page renders | `surfaces` | | Memory needs a model provider; browsing and editing memory are not covered |
| Overview renders | `surfaces` | | |
| Settings: account density | `agents` | | |
| Settings: API keys create, copy, revoke | `attention` (create) | `api-keys` | |
| Settings: security, processing, data render | `surfaces` | | Export download and restore: not yet |
| Design: evening strip on Home, engraved valley on first-use empty states, the setup dialog, the connection and not-found pages and the foot of lighter pages, pill actions, status pills, the floating top bar | | `app-design` | Desktop and phone widths |

## Adding a surface

1. Prefer the real stack for anything that writes or reads through the backend; use the mocked tier for failure injection, timing and layout.
2. Use the names the screen renders. A failing run writes the page's accessibility tree under `.e2e/artifacts/`, which is the quickest way to learn them.
3. Give every created title a unique suffix (`unique()` in `tests/stack/support/stack.ts`), so tests share one Space without ordering.
4. Add a row here.
