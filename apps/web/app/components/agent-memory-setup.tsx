
import { useEffect, useId, useRef, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { spaceMemoryInstructions, spaceAgentSetupPrompt, spaceAgentUpdatePrompt, spaceSetupCheckPrompt } from "../../lib/agent-memory-instructions";
import { Icon } from "./space/ui-icon";
import { spaceSkills, skillInstallPrompt } from "../../lib/space-skills";
import styles from "./agent-memory-setup.module.css";

const copyChoices = {
  setup: { name: "Setup prompt", text: spaceAgentSetupPrompt },
  update: { name: "Update prompt", text: spaceAgentUpdatePrompt },
  instructions: { name: "Instructions", text: spaceMemoryInstructions },
  check: { name: "Read-only check prompt", text: spaceSetupCheckPrompt },
};
type CopyChoice = keyof typeof copyChoices;
const setupTabs = ["Set up your agent", "Instructions", "Skills"];
const clients = [
  { name: "Claude Code", logo: "claude-code.svg" },
  { name: "Codex", logo: "codex.svg" },
  { name: "Cursor", logo: "cursor.svg" },
  { name: "Hermes", logo: "hermes-portrait.png" },
];

export function AgentMemorySetup({ heading = "Agent setup", hideHeading = false }: { heading?: string; hideHeading?: boolean }) {
  const id = useId();
  const [tab, setTab] = useState(0);
  const [selected, setSelected] = useState<CopyChoice>("instructions");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [manualCopy, setManualCopy] = useState(false);
  const [skillFallback, setSkillFallback] = useState("");
  const skillText = useRef<HTMLTextAreaElement>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const text = useRef<HTMLTextAreaElement>(null);
  useFeedback({
    error,
    notice,
    loading: busy ? "Copying…" : null,
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });
  useEffect(() => {
    if (manualCopy && tab === 1) text.current?.focus();
  }, [manualCopy, selected, tab]);
  useEffect(() => {
    if (skillFallback && tab === 2) skillText.current?.focus();
  }, [skillFallback, tab]);
  async function copySkill(name: string) {
    const prompt = skillInstallPrompt(name, new URL(`/skills/${name}.zip`, window.location.origin).href);
    setBusy(true);
    setError("");
    setNotice("");
    setSkillFallback("");
    try {
      await navigator.clipboard.writeText(prompt);
      setNotice(`${name} installation prompt copied. Paste it into your agent; no skill has been installed yet.`);
    } catch {
      setSkillFallback(prompt);
      setError("Could not copy automatically. Select the installation prompt below and copy it manually.");
    } finally {
      setBusy(false);
    }
  }
  function showInstructions(choice: CopyChoice) {
    setSelected(choice);
    setTab(1);
    tabs.current[1]?.focus();
  }
  async function copy(choice: CopyChoice) {
    setSelected(choice);
    setBusy(true);
    setError("");
    setNotice("");
    setManualCopy(false);
    try {
      await navigator.clipboard.writeText(copyChoices[choice].text);
      setNotice(choice === "instructions"
        ? "Instructions copied. Add them to the instruction file your agent uses."
        : "Prompt copied. Paste it into your connected agent to continue.");
    } catch {
      setTab(1);
      setManualCopy(true);
      setError("Could not copy automatically. Select the text below and copy it manually.");
    } finally {
      setBusy(false);
    }
  }
  return <section className={styles.setup} aria-label={heading} aria-busy={busy}>
    {!hideHeading && <h3 className={styles.title}>{heading}</h3>}
    <p className={styles.intro}>Make Space part of the way your agent works. Set up its memory instructions once, then keep useful context between sessions.</p>
    <div className={styles.tabs} role="tablist" aria-label="Agent setup options">
      {setupTabs.map((label, index) => <button
        type="button" role="tab" key={label} id={`${id}-tab-${index}`}
        aria-selected={tab === index} aria-controls={`${id}-panel-${index}`} tabIndex={tab === index ? 0 : -1}
        ref={node => { tabs.current[index] = node; }}
        onClick={() => setTab(index)}
        onKeyDown={event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? setupTabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + setupTabs.length) % setupTabs.length;
          setTab(next); tabs.current[next]?.focus();
        }}
      >{label}</button>)}
    </div>
    <div id={`${id}-panel-0`} role="tabpanel" aria-labelledby={`${id}-tab-0`} hidden={tab !== 0}>
      <div className={styles.card}>
        <h4>Set up your agent</h4>
        <p>Paste the setup prompt into your connected agent to add Space memory instructions. It will preserve your existing instructions and guide you through any manual steps.</p>
        <div className={styles.clients} aria-label="Agent examples">
          {clients.map(client => <span key={client.name}><img src={`/integrations/${client.logo}`} alt="" width={22} height={22} />{client.name}</span>)}
          <span>or your preferred agent</span>
        </div>
        <div className={styles.actions}>
          <button type="button" className={styles.primary} disabled={busy} onClick={() => void copy("setup")}><Icon name="copy" size={17} />Copy setup prompt</button>
          <button type="button" className={styles.textLink} onClick={() => showInstructions("instructions")}>Setup instructions <Icon name="arrow" size={16} /></button>
        </div>
        <div className={styles.followUp}>
          <p>Once connected, ask your agent to retrieve the context and open tasks for your project.</p>
          <button type="button" className={styles.textLink} disabled={busy} onClick={() => void copy("check")}>Copy check prompt <Icon name="copy" size={14} /></button>
        </div>
      </div>
      <div className={styles.card}>
        <h4>Update your instructions</h4>
        <p>Already set up? Ask your agent to review its Space instructions while preserving connection settings and personal edits. It will ask before making changes.</p>
        <div className={styles.actions}>
          <button type="button" className={styles.primary} disabled={busy} onClick={() => void copy("update")}><Icon name="copy" size={17} />Copy update prompt</button>
          <button type="button" className={styles.textLink} onClick={() => showInstructions("update")}>Update instructions <Icon name="arrow" size={16} /></button>
        </div>
      </div>
    </div>
    <div id={`${id}-panel-1`} role="tabpanel" aria-labelledby={`${id}-tab-1`} hidden={tab !== 1}>
      <div className={styles.card}>
        <h4>Give your agent a memory habit</h4>
        <p>Add the instructions to <code>AGENTS.md</code>, <code>CLAUDE.md</code>, or the instruction settings your agent supports. Connecting to MCP alone does not install these instructions.</p>
        <label htmlFor={`${id}-choice`}>Text to copy</label>
        <select id={`${id}-choice`} value={selected} onChange={event => { setSelected(event.target.value as CopyChoice); setManualCopy(false); }}>
          {Object.entries(copyChoices).map(([value, choice]) => <option key={value} value={value}>{choice.name}</option>)}
        </select>
        <label htmlFor={`${id}-text`}>{copyChoices[selected].name}</label>
        <textarea ref={text} id={`${id}-text`} readOnly rows={12} value={copyChoices[selected].text} spellCheck={false} />
        <div className={styles.actions}><button type="button" className={styles.primary} disabled={busy} onClick={() => void copy(selected)}><Icon name="copy" size={17} />{selected === "instructions" ? "Copy instructions" : `Copy ${copyChoices[selected].name.toLowerCase()}`}</button></div>
      </div>
    </div>
    <div id={`${id}-panel-2`} role="tabpanel" aria-labelledby={`${id}-tab-2`} hidden={tab !== 2}>
      <div className={styles.card}>
        <h4>Choose your Space skills</h4>
        <p>Optional workflows for your connected agent. Install the ones you need, not the whole list. Each download includes its instructions and Codex display metadata.</p>
        <p className={styles.skillHelp}>Download a skill and place its folder in your client’s skill directory, or copy an installation prompt for your agent. Skills do not connect MCP, grant access, or run automatically.</p>
        <ul className={styles.skillList}>
          {spaceSkills.map(skill => <li key={skill.name}>
            <div><h5>{skill.title}</h5><code>{skill.name}</code><p>{skill.description}</p></div>
            <div className={styles.skillActions}>
              <button type="button" className={styles.textLink} disabled={busy} aria-label={`Copy install prompt for ${skill.name}`} onClick={() => void copySkill(skill.name)}><Icon name="copy" size={15} />Copy install prompt</button>
              <a className={styles.textLink} href={`/skills/${skill.name}.zip`} download aria-label={`Download ${skill.name}`}>Download <Icon name="arrow" size={15} /></a>
            </div>
          </li>)}
        </ul>
        {skillFallback && <div>
          <label htmlFor={`${id}-skill-text`}>Skill installation prompt</label>
          <textarea ref={skillText} id={`${id}-skill-text`} value={skillFallback} readOnly rows={9} />
        </div>}
      </div>
    </div>
    <p className={styles.hint}>Copying a prompt does not edit files or run tools. The connection check is read-only; saving needs write access. Client connection support varies.</p>
  </section>;
}
