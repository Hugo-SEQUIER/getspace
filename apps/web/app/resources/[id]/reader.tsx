
import Link from "@web/lib/navigation";
import { useEffect, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { api, ApiError, message } from "../../../lib/client";
import { Markdown } from "../../components/space/ui";
import { Unlock } from "../../components/unlock";

type Resource = {
  id: string;
  title: string;
  body: string;
  revision: number;
  author: string;
  kind: string;
  status: string;
  memory?: { claims: { id: string; index: string; value: string; edited?: boolean }[] };
  recall?: {
    category: string;
    excluded: boolean;
    sources: { resource_id: string; revision: number }[];
  };
};

export function ResourceReader({ id, spaceId, revision }: {
  id: string;
  spaceId?: string;
  revision?: string;
}) {
  const [resource, setResource] = useState<Resource | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [locked, setLocked] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useFeedback({
    error,
    loading: loading ? "Loading source…" : null,
    clearError: () => setError(""),
  });
  useEffect(() => {
    let active = true;
    setResource(null);
    setError("");
    setLocked(false);
    setLoading(true);
    const uuid = /^[a-f0-9-]{36}$/;
    if (!uuid.test(id) || !spaceId || !uuid.test(spaceId) ||
        (revision !== undefined && !/^[1-9][0-9]*$/.test(revision))) {
      setError("This source link is invalid.");
      setLoading(false);
      return;
    }
    api<Resource>(`spaces/${spaceId}/resources/${id}${revision ? `?revision=${revision}` : ""}`)
      .then(value => { if (active) setResource(value); })
      .catch(error => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) setLocked(true);
        else setError(message(error));
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id, spaceId, revision, attempt]);

  // A locked browser unlocks here and reopens the same source.
  if (locked)
    return <main className="sp-auth-page"><Unlock onUnlocked={() => setAttempt(value => value + 1)} /></main>;
  return (
    <main className="source-reader" aria-busy={loading}>
      <Link href="/app">← Open Space</Link>
      {loading ? <div aria-label="Source document"><span className="sp-loading-orbit" aria-hidden="true" /></div> :
        !resource ? <>
          <h1>Source unavailable</h1>
          <p>Check that the link is complete, then reopen it.</p>
        </> : <>
          <p>Saved revision {resource.revision} · {resource.author === "agent" ? "Agent authored" : "User authored"}</p>
          <h1>{resource.title}</h1>
          {resource.recall?.category === "handoff" && <p>Session handoff · recorded context</p>}
          {resource.recall?.excluded && <p>This revision excludes this resource from recall.</p>}
          {resource.kind === "task" && <p>Status at this revision: {resource.status}</p>}
          <p><Link href={`/resources/${id}?space_id=${spaceId}`}>Read current revision</Link></p>
          {resource.memory ? <>
            <p>Memory at this saved revision. <Link href="/app/memory">Open current memory</Link></p>
            {resource.memory.claims.map(claim => <section id={`memory-${claim.id}`} key={claim.id}>
              <h2>{claim.index}</h2><p>{claim.value}</p>{claim.edited && <p>Edited</p>}
            </section>)}
          </> : <Markdown text={resource.body} />}
          {!!resource.recall?.sources.length && <nav aria-label="Evidence">
            <h2>Sources</h2>
            <ul>{resource.recall.sources.map(ref => <li key={`${ref.resource_id}:${ref.revision}`}>
              <Link href={`/resources/${ref.resource_id}?space_id=${spaceId}&revision=${ref.revision}`}>
                {ref.resource_id} · revision {ref.revision}
              </Link>
            </li>)}</ul>
          </nav>}
        </>}
    </main>
  );
}
