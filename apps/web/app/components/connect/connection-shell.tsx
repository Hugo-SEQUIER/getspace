import type { ReactNode } from "react";
import { docsLinks } from "../../../lib/docs-links";
import { SpaceMark, Wordmark } from "../space/brand";
import { Icon, type IconName } from "../space/ui-icon";
import styles from "./connection.module.css";

export function ConnectionShell({
  children,
  wide = false,
}: {
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <main className={styles.shell}>
      <header className={styles.header}>
        <a href="/app" aria-label="Space home">
          <Wordmark />
        </a>
        <a
          className={styles.help}
          href={docsLinks.connectingAgents}
          target="_blank"
          rel="noreferrer"
        >
          Connection help <Icon name="external" size={14} />
        </a>
      </header>
      <div className={`${styles.content} ${wide ? styles.wideContent : ""}`}>
        {children}
        <p className={styles.footer}>
          <Icon name="lock" size={13} /> Your space. Your choice of agents.
        </p>
      </div>
    </main>
  );
}

export function ConnectionMarks({
  agent = "",
  success = false,
}: {
  agent?: string;
  success?: boolean;
}) {
  const name = agent.toLowerCase();
  const icon: IconName = /^(codex|cursor)$/.test(name)
    ? "code"
    : /^claude( code)?$/.test(name)
      ? "sun"
      : name === "chatgpt"
        ? "chat"
        : "agents";
  return (
    <div className={styles.marks} aria-hidden="true">
      <span className={styles.agentMark} data-agent={icon}>
        <Icon name={icon} size={26} />
      </span>
      <span className={styles.connector}>
        {success ? (
          <Icon name="check" size={16} />
        ) : (
          <Icon name="link" size={16} />
        )}
      </span>
      <span className={styles.spaceMark}>
        <SpaceMark />
      </span>
    </div>
  );
}
