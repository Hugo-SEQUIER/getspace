
import { useId, useRef, useState } from "react";
import { Icon, type IconName } from "./ui-icon";
import {
  emojiPageIcons,
  iconColors,
  svgPageIcons,
  type PageIconChoice,
  type PageIconColor,
} from "./page-icons";

export function PageIconPicker({
  value,
  fallback,
  onChange,
}: {
  value?: PageIconChoice;
  fallback: IconName;
  onChange: (value?: PageIconChoice) => void;
}) {
  const [mode, setMode] = useState<"svg" | "emoji">(
    typeof value === "string" ? "emoji" : "svg",
  );
  const [query, setQuery] = useState("");
  const [pendingColor, setPendingColor] = useState<PageIconColor>("default");
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const id = useId();
  const selectedColor =
    value && typeof value !== "string" ? value.color : pendingColor;
  const color = iconColors[selectedColor].value;
  const normalizedQuery = query.trim().toLowerCase();
  const icons = svgPageIcons.filter((icon) =>
    `${icon.label} ${icon.keywords}`.toLowerCase().includes(normalizedQuery),
  );
  const emoji = emojiPageIcons.filter(([symbol, name]) =>
    `${symbol} ${name}`.toLowerCase().includes(normalizedQuery),
  );
  return (
    <div className="sp-page-icon-picker">
      <div className="sp-icon-picker-label">
        <span>Page icon</span>
        <button
          type="button"
          className="sp-text-button"
          disabled={!value}
          onClick={() => {
            setPendingColor("default");
            onChange();
          }}
        >
          Reset icon
        </button>
      </div>
      <div className="sp-icon-tabs" role="tablist" aria-label="Icon type">
        {(["svg", "emoji"] as const).map((tab, index) => (
          <button
            type="button"
            key={tab}
            ref={(el) => {
              tabs.current[index] = el;
            }}
            id={`${id}-${tab}`}
            role="tab"
            aria-selected={mode === tab}
            aria-controls={`${id}-panel`}
            tabIndex={mode === tab ? 0 : -1}
            onClick={() => {
              setMode(tab);
              setQuery("");
            }}
            onKeyDown={(event) => {
              if (
                !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
              )
                return;
              event.preventDefault();
              const next =
                event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
              setMode(next === 0 ? "svg" : "emoji");
              setQuery("");
              tabs.current[next]?.focus();
            }}
          >
            {tab === "svg" ? (
              <>
                <Icon name="star" size={15} />
                Icons
              </>
            ) : (
              <>
                <span aria-hidden="true" className="sp-emoji-tab-mark">
                  ☺
                </span>
                Emoji
              </>
            )}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${mode}`}>
        <label className="sp-sidebar-page-search sp-icon-search">
          <Icon name="search" size={16} />
          <input
            type="search"
            aria-label={mode === "svg" ? "Search icons" : "Search emoji"}
            placeholder={mode === "svg" ? "Search icons…" : "Search emoji…"}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        {mode === "svg" && (
          <fieldset className="sp-icon-colors">
            <legend className="sr-only">Icon color</legend>
            {(
              Object.entries(iconColors) as [
                PageIconColor,
                (typeof iconColors)[PageIconColor],
              ][]
            ).map(([key, option]) => (
              <label key={key} title={option.label}>
                <input
                  type="radio"
                  name={`${id}-color`}
                  value={key}
                  checked={selectedColor === key}
                  aria-label={option.label}
                  onChange={() => {
                    setPendingColor(key);
                    if (value && typeof value !== "string")
                      onChange({ ...value, color: key });
                    else if (!value)
                      onChange({
                        type: "svg",
                        name:
                          svgPageIcons.find((item) => item.name === fallback)
                            ?.name ?? "document",
                        color: key,
                      });
                  }}
                />
                <span
                  className="sp-icon-color-dot"
                  style={{ backgroundColor: option.value }}
                >
                  {selectedColor === key && <Icon name="check" size={12} />}
                </span>
              </label>
            ))}
          </fieldset>
        )}
        <div
          className={`sp-icon-picker ${mode === "svg" ? "sp-svg-picker" : "sp-emoji-picker"}`}
          role="group"
          aria-label={mode === "svg" ? "SVG icons" : "Emoji icons"}
        >
          {mode === "svg"
            ? icons.map((icon) => (
                <button
                  type="button"
                  key={icon.name}
                  aria-label={`${icon.label} icon`}
                  title={icon.label}
                  aria-pressed={
                    typeof value === "object" && value.name === icon.name
                  }
                  onClick={() =>
                    onChange({
                      type: "svg",
                      name: icon.name,
                      color: selectedColor,
                    })
                  }
                >
                  <Icon
                    name={icon.name}
                    size={23}
                    style={{ color, strokeWidth: 1.8 }}
                  />
                </button>
              ))
            : emoji.map(([symbol, name]) => (
                <button
                  type="button"
                  key={name}
                  aria-label={`${name} icon`}
                  title={name}
                  aria-pressed={value === symbol}
                  onClick={() => {
                    setPendingColor(selectedColor);
                    onChange(symbol);
                  }}
                >
                  {symbol}
                </button>
              ))}
          {(mode === "svg" ? !icons.length : !emoji.length) && (
            <p className="sp-icon-empty">
              No {mode === "svg" ? "icons" : "emoji"} found. Try another word.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
