
import { useEffect, useId, useRef, useState } from "react";
import { harnesses, harnessName } from "../../../lib/space/agents";
import { HarnessLogo } from "./harness-logo";
import { Icon } from "./ui-icon";
import styles from "./harness-select.module.css";

export function HarnessSelect({
  value,
  onChange,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(value);
  const options = harnesses.filter((h) =>
    `${h.name} ${h.id}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const activeId = options.find((h) => h.id === active)?.id ?? options[0]?.id;

  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target))
        setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  useEffect(() => {
    if (open && activeId)
      document
        .getElementById(`${id}-${activeId}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [open, activeId, id]);

  function choose(next: string) {
    onChange(next);
    setOpen(false);
    trigger.current?.focus();
  }

  return (
    <div
      className={styles.field}
      ref={root}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (open && event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <span id={`${id}-label`}>Harness</span>
      <button
        ref={trigger}
        type="button"
        className={styles.trigger}
        disabled={disabled}
        aria-labelledby={`${id}-label ${id}-value`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-options` : undefined}
        onClick={() => {
          setQuery("");
          setActive(value);
          setOpen(!open);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setQuery("");
            setActive(value);
            setOpen(true);
          }
        }}
      >
        <HarnessLogo harness={value} />
        <span id={`${id}-value`}>{harnessName(value)}</span>
        <Icon name="chevron" size={16} />
      </button>
      {open && !disabled && (
        <div className={styles.panel}>
          <input
            ref={search}
            className={styles.search}
            role="combobox"
            aria-label="Search harnesses"
            aria-expanded="true"
            aria-autocomplete="list"
            aria-controls={`${id}-options`}
            aria-activedescendant={activeId ? `${id}-${activeId}` : undefined}
            value={query}
            placeholder="Search harnesses…"
            autoComplete="off"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              const index = options.findIndex((h) => h.id === activeId);
              if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
                event.preventDefault();
                const next =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? options.length - 1
                      : (index +
                          (event.key === "ArrowDown" ? 1 : -1) +
                          options.length) %
                        options.length;
                if (options[next]) setActive(options[next].id);
              } else if (event.key === "Enter") {
                event.preventDefault();
                if (activeId) choose(activeId);
              }
            }}
          />
          <div
            id={`${id}-options`}
            role="listbox"
            aria-label="Harnesses"
            className={styles.options}
          >
            {options.map((h) => (
              <button
                key={h.id}
                id={`${id}-${h.id}`}
                type="button"
                role="option"
                tabIndex={-1}
                aria-selected={value === h.id}
                className={styles.option}
                data-active={activeId === h.id}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => choose(h.id)}
              >
                <HarnessLogo harness={h.id} />
                <span>{h.name}</span>
                {value === h.id && <Icon name="check" size={15} />}
              </button>
            ))}
          </div>
          {!options.length && (
            <p className={styles.empty} role="status">
              No matching harnesses. Try another name.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
