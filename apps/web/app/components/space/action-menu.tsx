import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "./ui-icon";
import "./action-menu.css";

export type ActionMenuItem = {
  label: string;
  icon?: IconName;
  onSelect: () => void;
  /** A red item: deleting, removing. */
  danger?: boolean;
  disabled?: boolean;
};

// Parameters: the menu's accessible name, its items, and what the trigger shows (the ··· glyph by
//   default).
// What it does: one small menu for the actions of a single item, as the design rules define the ···
//   control. It opens under the trigger, floats above scrolling containers, moves with the arrow
//   keys, and closes on Escape, on a choice, or on a press outside.
// Output: the trigger button and, while open, the menu.
export function ActionMenu({
  label,
  items,
  children,
  className = "",
  disabled = false,
  align = "end",
}: {
  label: string;
  items: ActionMenuItem[];
  children?: ReactNode;
  className?: string;
  disabled?: boolean;
  /** Which edge of the trigger the menu lines up with. */
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();

  // The menu follows its trigger: it is placed from the trigger's box and placed again whenever
  // the page scrolls or resizes, so it never floats away from the row it belongs to.
  function place() {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const below = window.innerHeight - rect.bottom > 260;
    setPosition({
      position: "fixed",
      ...(below ? { top: rect.bottom + 4 } : { bottom: window.innerHeight - rect.top + 4 }),
      ...(align === "end" ? { right: Math.max(8, window.innerWidth - rect.right) } : { left: Math.max(8, rect.left) }),
    });
  }
  useLayoutEffect(() => {
    if (!open) return;
    place();
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus({ preventScroll: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, align]);

  useEffect(() => {
    if (!open) return;
    const pointer = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      if (menu.current?.contains(event.target) || trigger.current?.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", pointer, true);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("pointerdown", pointer, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function closeAndRefocus() {
    setOpen(false);
    trigger.current?.focus();
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={`sp-icon-button sp-menu-trigger ${className}`}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        {children ?? <Icon name="more" size={16} />}
      </button>
      {open && createPortal(
        <div
          ref={menu}
          id={id}
          className="sp-menu"
          role="menu"
          aria-label={label}
          style={position}
          onKeyDown={(event) => {
            const options = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)')];
            const index = options.indexOf(document.activeElement as HTMLElement);
            if (event.key === "Escape") {
              event.preventDefault();
              closeAndRefocus();
            } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              options[(index + step + options.length) % options.length]?.focus();
            } else if (event.key === "Home" || event.key === "End") {
              event.preventDefault();
              options[event.key === "Home" ? 0 : options.length - 1]?.focus();
            } else if (event.key === "Tab") {
              setOpen(false);
            }
          }}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={`sp-menu-item ${item.danger ? "is-danger" : ""}`}
              disabled={item.disabled}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.icon && <Icon name={item.icon} size={16} />}
              <span>{item.label}</span>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}
