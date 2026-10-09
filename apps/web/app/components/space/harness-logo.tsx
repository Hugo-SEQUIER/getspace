import { harnessLogo } from "../../../lib/space/agents";
import { Icon } from "./ui-icon";
import styles from "./harness-select.module.css";

export function HarnessLogo({
  harness,
  className = "",
}: {
  harness: string;
  className?: string;
}) {
  const src = harnessLogo(harness);
  return (
    <span
      className={`${styles.logo} ${className}`}
      data-portrait={harness === "hermes"}
      aria-hidden="true"
    >
      {src ? (
        <img src={src} alt="" width={24} height={24} />
      ) : (
        <Icon name="agents" size={20} />
      )}
    </span>
  );
}
