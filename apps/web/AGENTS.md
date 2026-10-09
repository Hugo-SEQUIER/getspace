# Frontend instructions

The frontend uses TanStack Start, Router, Query, and Effect. Route entry points live in `src/routes`; reusable UI components remain under `app/components`.

Start is isomorphic: route loaders and components can execute in browsers. Keep the access key and every other secret inside server handlers (`lib/access.ts`, `app/api`). Preserve the signed session cookie, the access-mode check, CSRF (Origin) validation, and private `no-store` responses. The browser never receives the access key.

Prefer independent route modules, progressive data loading, bounded background reads, and cancellation. Do not introduce eager prefetching of all resource bodies.

Run the web unit tests, type check, production build, and the e2e tests (`npx e2e run`) after framework changes. Follow the root AGENTS.md for shared-checkout and Markdown rules.
