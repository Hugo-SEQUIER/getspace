import type { ReactNode } from "react";
import { Wordmark } from "./space/brand";
import "../valley.css";
import "./status-page.css";

// Not-found and error pages: the wordmark, one message and a way back, over the
// engraved valley. Plain links, so they still work when the router could not load.
// The static website writes the same markup to 404.html (scripts/build-website.mjs).
export function StatusPage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="sp-status-page sp-valley">
      <a className="sp-status-brand" href="/" aria-label="Space home">
        <Wordmark />
      </a>
      <div className="sp-status-body">
        <h1>{title}</h1>
        {children}
        <a className="sp-status-action" href="/">Return to Space</a>
      </div>
    </main>
  );
}
