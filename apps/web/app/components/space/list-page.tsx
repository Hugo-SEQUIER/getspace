import { useState } from "react";
import { Icon } from "./ui-icon";
import "./list-page.css";

/** Bound DOM work independently of how much data is loaded or searched. */
export function useListPage<T>(items: readonly T[], filterKey: string, size = 50) {
  const [selection, setSelection] = useState({ key: filterKey, page: 0 });
  const pages = Math.max(1, Math.ceil(items.length / size));
  const page = selection.key === filterKey ? Math.min(selection.page, pages - 1) : 0;
  return {
    items: items.slice(page * size, (page + 1) * size), page, pages,
    total: items.length, size,
    setPage: (next: number) => setSelection({ key: filterKey, page: Math.max(0, Math.min(next, pages - 1)) }),
  };
}

export function ListPageControls({ page, pages, total, size, setPage }: {
  page: number; pages: number; total: number; size: number; setPage: (page: number) => void;
}) {
  if (pages <= 1) return null;
  return <nav className="sp-pager" aria-label="List pages">
    <span className="sp-pager-range" aria-live="polite">
      {(page * size + 1).toLocaleString()}–{Math.min((page + 1) * size, total).toLocaleString()}
      <span className="sp-pager-total"> of {total.toLocaleString()}</span>
    </span>
    <span className="sp-pager-buttons">
      <button type="button" aria-label="Previous page" title="Previous page" disabled={page === 0} onClick={() => setPage(page - 1)}>
        <Icon name="chevron" size={16} style={{ transform: "rotate(180deg)" }} />
      </button>
      <button type="button" aria-label="Next page" title="Next page" disabled={page + 1 === pages} onClick={() => setPage(page + 1)}>
        <Icon name="chevron" size={16} />
      </button>
    </span>
  </nav>;
}
