import Link, { useRouter, useSearchParams } from "@web/lib/navigation";
import { useMemo, useState } from "react";
import {
  EVIDENCE, LANES, cadenceLabel, filterEntries, laneLabel, localToday, percent, reviewState,
  type Evidence, type LabEntry, type LabStatus, type System, type SystemsArea,
} from "../../../lib/space/systems";
import { useFeedback } from "../notifications";
import { Empty } from "./ui";
import { Icon } from "./ui-icon";
import { SystemDialog } from "./systems-forms";
import { EvidenceMeter, LaneMark, PipelineBar, StatusPill, dayLabel, dayPhrase, useSystemsArea } from "./systems-parts";
import { SystemPage } from "./system-page";
import { ActivityFeed } from "./systems-activity";
import { SystemsBand } from "./systems-band";
import styles from "./systems.module.css";

// Parameters: the workspace base path and, on a system's page, its id.
// What it does: routes /systems to the area and /systems/{id} to one system.
// Output: the page.
export function SystemsView({ base, id }: { base: string; id?: string }) {
  return id ? <SystemPage base={base} id={id} /> : <SystemsLibrary base={base} />;
}

function plural(count: number, one: string, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`;
}

function SystemsLibrary({ base }: { base: string }) {
  const { area, error, canWrite, keep, spaceId, readOnlyNote } = useSystemsArea();
  const router = useRouter();
  const params = useSearchParams();
  const tab = params.get("tab") === "lab" ? "lab" : "systems";
  const [creating, setCreating] = useState(false);
  useFeedback({ error });
  return (
    <div className={styles.page}>
      <div className="sp-work-heading">
        <div>
          <h1>Systems</h1>
          <p>The work you repeat, its goal, and the lab that tests how to do it better. Agents recall only what is proven.</p>
        </div>
        {canWrite && (
          <div className={styles.actions}>
            <button className="sp-primary" onClick={() => setCreating(true)}>
              <Icon name="plus" size={16} />
              New system
            </button>
          </div>
        )}
      </div>
      {readOnlyNote && <p className={styles.note}>{readOnlyNote}</p>}
      {area === null ? (
        <p className={styles.loading} role="status">{error ? "Systems are unavailable right now." : "Loading systems…"}</p>
      ) : !area.systems.length ? (
        <Empty valley icon="system" title="No systems yet">
          <p>A system is work you repeat, like X posting or cold email, with a goal to move. Its lab holds what you are testing; only what works, or does not, reaches your agents.</p>
        </Empty>
      ) : (
        <>
          <SystemsBand systems={area.systems} />
          <ActivityFeed base={base} spaceId={spaceId} systemNames={new Map(area.systems.map((system) => [system.id, system.name]))} />
          <div className={styles.tabs} role="group" aria-label="View">
            <button type="button" aria-pressed={tab === "systems"} onClick={() => router.replace(`${base}/systems`, { scroll: false })}>
              Systems<span>{area.systems.length}</span>
            </button>
            <button type="button" aria-pressed={tab === "lab"} onClick={() => router.replace(`${base}/systems?tab=lab`, { scroll: false })}>
              Lab<span>{area.entries.length}</span>
            </button>
          </div>
          {tab === "systems" ? <SystemRows base={base} systems={area.systems} /> : <LabList base={base} area={area} />}
        </>
      )}
      {creating && spaceId && (
        <SystemDialog spaceId={spaceId} onClose={() => setCreating(false)} onSaved={(system) => {
          keep(system);
          router.push(`${base}/systems/${system.id}`);
        }} />
      )}
    </div>
  );
}

function Review({ system, today }: { system: System; today: string }) {
  const review = reviewState(system, today);
  if (!system.review_cadence) return <span className={styles.muted}>No set review</span>;
  return (
    <>
      <span>{cadenceLabel(system.review_cadence)}</span>
      {review.due ? <span className={styles.due}><Icon name="clock" size={12} />Review due</span>
        : review.next ? <small>Next review {dayPhrase(review.next)}</small>
          : system.last_review ? <small>Reviewed {dayLabel(system.last_review)}</small> : null}
    </>
  );
}

function SystemRows({ base, systems }: { base: string; systems: System[] }) {
  const today = localToday();
  return (
    <ul className={styles.rows} aria-label="Systems">
      {systems.map((system) => (
        <li key={system.id}>
          <Link className={styles.row} href={`${base}/systems/${system.id}`}>
            <span className={styles.rowStatus}><StatusPill status={system.status} /></span>
            <span className={styles.rowName}>
              <strong>{system.name}</strong>
              {system.description && <small>{system.description}</small>}
            </span>
            <span className={styles.rowGoal}>
              <small>{system.goal_metric || "No goal yet"}</small>
              {system.goal_metric && (
                <span><strong>{system.current_value || "–"}</strong><span aria-label="target"> → {system.goal_target || "no target"}</span></span>
              )}
            </span>
            <span className={styles.rowReview}><Review system={system} today={today} /></span>
            <span className={styles.rowLab}>
              <PipelineBar stats={system.stats} />
              <small>{system.stats.entries ? `${system.stats.testing} testing · ${system.stats.verdicts} ${system.stats.verdicts === 1 ? "verdict" : "verdicts"}` : "Empty lab"}</small>
            </span>
            <span className={styles.rowReuse}>
              <strong>{percent(system.stats.reuse_share)}</strong>
              <small>reused</small>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

const PAGE = 50;

// Parameters: the whole area.
// What it does: lists every lab entry across systems, newest change first, with filters by
// system, domain, lane and evidence and a search over the sentence and its notes. Each row
// opens the entry on its system's page.
// Output: a section.
function LabList({ base, area }: { base: string; area: SystemsArea }) {
  const [query, setQuery] = useState("");
  const [system, setSystem] = useState("");
  const [domain, setDomain] = useState("");
  const [status, setStatus] = useState<LabStatus | "">("");
  const [evidence, setEvidence] = useState<Evidence | "">("");
  const [shown, setShown] = useState(PAGE);
  const names = useMemo(() => new Map(area.systems.map((item) => [item.id, item.name])), [area.systems]);
  const listed = useMemo(() => filterEntries(area.entries, { query, system, domain, status, evidence }), [area.entries, query, system, domain, status, evidence]);
  const filtered = !!(query.trim() || system || domain || status || evidence);
  const clear = () => { setQuery(""); setSystem(""); setDomain(""); setStatus(""); setEvidence(""); };
  return (
    <section aria-label="Lab entries across systems">
      <div className={styles.filters}>
        <label className="sp-search">
          <Icon name="search" size={15} />
          <input aria-label="Search the lab" placeholder="Search the lab…" value={query} onChange={(e) => { setQuery(e.target.value); setShown(PAGE); }} />
        </label>
        <select aria-label="System" value={system} onChange={(e) => setSystem(e.target.value)}>
          <option value="">All systems</option>
          {area.systems.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <select aria-label="Domain" value={domain} onChange={(e) => setDomain(e.target.value)}>
          <option value="">All domains</option>
          {area.domains.map((item) => <option key={item}>{item}</option>)}
        </select>
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value as LabStatus | "")}>
          <option value="">Every lane</option>
          {LANES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <select aria-label="Evidence" value={evidence} onChange={(e) => setEvidence(e.target.value as Evidence | "")}>
          <option value="">Any evidence</option>
          {EVIDENCE.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <span className={styles.count}>{filtered ? `${listed.length} of ${plural(area.entries.length, "entry", "entries")}` : plural(area.entries.length, "entry", "entries")}</span>
      </div>
      {!listed.length ? (
        <div className={styles.noMatch} role="status">
          <p>{filtered ? "No entries match these filters." : "The lab is empty. Open a system to add what it should test."}</p>
          {filtered && <button onClick={clear}>Clear filters</button>}
        </div>
      ) : (
        <>
          <ul className={styles.labRows}>
            {listed.slice(0, shown).map((entry) => <LabRow key={entry.id} base={base} entry={entry} systemName={names.get(entry.system_id) ?? "Archived system"} />)}
          </ul>
          {listed.length > shown && <button className={styles.more} onClick={() => setShown((count) => count + PAGE)}>Show {Math.min(PAGE, listed.length - shown)} more</button>}
        </>
      )}
    </section>
  );
}

function LabRow({ base, entry, systemName }: { base: string; entry: LabEntry; systemName: string }) {
  return (
    <li>
      <Link className={styles.labRow} href={`${base}/systems/${entry.system_id}?entry=${entry.id}`}>
        <span className={styles.labLane}><LaneMark status={entry.status} />{laneLabel(entry.status)}</span>
        <span className={styles.labSentence}>
          <strong>{entry.name}</strong>
          <small>{systemName} · {entry.domain}{entry.retries > 0 && ` · ${plural(entry.retries, "retry", "retries")}`}</small>
        </span>
        <EvidenceMeter evidence={entry.evidence} />
        <small className={styles.labUses}>
          {entry.uses.length ? `Used ${entry.uses.length}× · last ${dayLabel(entry.last_used!)}` : entry.verdict ? "Not used yet" : `Updated ${dayPhrase(entry.updated_at)}`}
        </small>
      </Link>
    </li>
  );
}
