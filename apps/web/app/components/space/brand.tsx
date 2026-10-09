import { spaceMarkPath } from "../../../lib/space/brand-mark";

export function SpaceMark() {
  return (
    <svg
      className="sp-mark"
      viewBox="0 0 32 32"
      aria-hidden="true"
      focusable="false"
    >
      <rect
        className="sp-mark-frame"
        x="0.55"
        y="0.55"
        width="30.9"
        height="30.9"
        rx="8"
        fill="none"
        strokeWidth="1.1"
      />
      <path d={spaceMarkPath} fill="currentColor" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="sp-wordmark">
      <SpaceMark />
      <span>Space</span>
    </span>
  );
}
