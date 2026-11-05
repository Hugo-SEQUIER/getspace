import { useEffect, useState } from "react";
import {
  LANES, REUSE_TARGET, SYSTEM_STATUSES, areaSummary, odometerDigits, percent, type System,
} from "../../../lib/space/systems";
import { LaneMark, StatusMark } from "./systems-parts";
import styles from "./systems-band.module.css";

const DIGITS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];

// Parameters: a count.
// What it does: shows the count as rolling digits: when it changes after the first paint,
// each digit slides to its new value (300ms; at rest with reduced motion). Screen readers
// read the number itself.
// Output: an inline element.
export function Odometer({ value }: { value: number }) {
  // Only changes after the first paint roll, so nothing moves on page load.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setReady(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  const digits = odometerDigits(value);
  return (
    <span className={styles.odometer} data-ready={ready || undefined}>
      <span className="sr-only">{value}</span>
      <span aria-hidden="true" className={styles.digits}>
        {digits.map((digit, index) => (
          // Keyed from the right, so the units column stays the same element as the count grows.
          <span key={digits.length - index} className={styles.digit}>
            <span style={{ transform: `translateY(${-digit * 10}%)` }}>
              {DIGITS.map((n) => <span key={n}>{n}</span>)}
            </span>
          </span>
        ))}
      </span>
    </span>
  );
}

const RADIUS = 52;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** The reuse share as a mint ring, with the target as a notch across the ring. */
function ReuseRing({ share }: { share: number | null }) {
  const angle = REUSE_TARGET * 2 * Math.PI - Math.PI / 2;
  const notch = (r: number) => `${60 + r * Math.cos(angle)} ${60 + r * Math.sin(angle)}`;
  return (
    <svg className={styles.ring} viewBox="0 0 120 120" role="img"
      aria-label={`Reuse ${percent(share)}, target ${percent(REUSE_TARGET)}`}>
      <circle cx="60" cy="60" r={RADIUS} className={styles.ringTrack} />
      {share !== null && share > 0 && (
        <circle cx="60" cy="60" r={RADIUS} className={styles.ringValue}
          strokeDasharray={`${CIRCUMFERENCE * Math.min(share, 1)} ${CIRCUMFERENCE}`} transform="rotate(-90 60 60)" />
      )}
      <path d={`M${notch(RADIUS - 9)} L${notch(RADIUS + 9)}`} className={styles.ringNotch} />
      <text x="60" y="58" textAnchor="middle" className={styles.ringValueText}>{percent(share)}</text>
      <text x="60" y="76" textAnchor="middle" className={styles.ringLabel}>reused</text>
    </svg>
  );
}

// Parameters: the active systems with their statistics.
// What it does: the area's one night band, in the public site's night palette: how many
// systems are in each health state, the lab's totals by lane, and the reuse share of verdicts
// as a ring against its 30% target, above the engraved valley in mint dots.
// Output: a section.
export function SystemsBand({ systems }: { systems: System[] }) {
  const { health, lab, reuse } = areaSummary(systems);
  const totals = { testing: lab.testing, validated: lab.works, invalidated: lab.does_not_work, retired: lab.retired };
  const onTarget = reuse.share !== null && reuse.share >= REUSE_TARGET;
  return (
    <section className={styles.band} aria-label="Health of your systems">
      <div className={styles.group}>
        <h2>Health</h2>
        <ul>
          {SYSTEM_STATUSES.map(({ value, label }) => (
            <li key={value} data-zero={!health[value] || undefined}>
              <StatusMark status={value} />
              <span>{label}</span>
              <strong><Odometer value={health[value]} /></strong>
            </li>
          ))}
        </ul>
      </div>
      <div className={styles.group}>
        <h2>Lab</h2>
        <ul>
          {LANES.map(({ value, label }) => (
            <li key={value} data-zero={!totals[value] || undefined}>
              <LaneMark status={value} />
              <span>{label}</span>
              <strong><Odometer value={totals[value]} /></strong>
            </li>
          ))}
        </ul>
      </div>
      <div className={`${styles.group} ${styles.reuse}`}>
        <h2>Reuse</h2>
        <div className={styles.reuseBody}>
          <ReuseRing share={reuse.share} />
          <p>
            <strong>{reuse.share === null ? "No verdicts yet" : onTarget ? "On target" : "Below target"}</strong>
            <span>{reuse.verdicts ? `${reuse.reused} of ${reuse.verdicts} ${reuse.verdicts === 1 ? "verdict" : "verdicts"} used within 30 days of promotion` : "A verdict counts once it is used within 30 days."}</span>
            <span>Target {percent(REUSE_TARGET)}</span>
          </p>
        </div>
      </div>
    </section>
  );
}
