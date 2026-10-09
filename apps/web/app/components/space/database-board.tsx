import { useRef, useState, type ReactNode } from "react";
import {
  DndContext, DragOverlay, KeyboardSensor, MouseSensor, TouchSensor,
  pointerWithin, rectIntersection, useDndMonitor, useDraggable, useDroppable, useSensor, useSensors,
  type KeyboardCoordinateGetter,
} from "@dnd-kit/core";
import { optionColor, type DatabaseField, type DatabaseRecord, type WorkspaceDatabase } from "../../../lib/space/databases";
import { Icon } from "./ui-icon";
import { RecordValue } from "./database-table";
import { InlineRecordDraft } from "./database-inline-draft";

type Group = string | null;
// Like Notion, each column renders its first cards and loads more on request.
const COLUMN_PAGE = 25;
const targetId = (group: Group) => `database-group:${JSON.stringify(group)}`;

function groupCoordinates(groups: Group[]): KeyboardCoordinateGetter {
  return (event, { context, currentCoordinates }) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.code)) return;
    event.preventDefault();
    const current = groups.findIndex(group => targetId(group) === (context.over?.id ?? context.active?.data.current?.groupTarget));
    const next = Math.max(0, Math.min(groups.length - 1, current + (event.code === "ArrowRight" ? 1 : -1)));
    const target = context.droppableRects.get(targetId(groups[next]));
    const rect = context.collisionRect;
    if (!target || !rect) return;
    return {
      x: currentCoordinates.x + target.left + target.width / 2 - (rect.left + rect.width / 2),
      y: currentCoordinates.y + target.top + target.height / 2 - (rect.top + rect.height / 2),
    };
  };
}

function BoardCard({ visibleFields, field, row, canWrite, open }: {
  visibleFields: DatabaseField[];
  field: DatabaseField;
  row: DatabaseRecord;
  canWrite: boolean;
  open: (record: DatabaseRecord) => void;
}) {
  const group = String(row.values[field.id] ?? "") || null;
  const held = useRef(false);
  useDndMonitor({ onDragStart: ({ active }) => { if (active.id === row.id) held.current = true; } });
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } = useDraggable({
    id: row.id, data: { record: row, groupTarget: targetId(group) }, disabled: !canWrite,
  });
  const properties = visibleFields.filter(property =>
    row.values[property.id] !== null && row.values[property.id] !== undefined && row.values[property.id] !== "");
  return <article
    ref={setNodeRef} className={`sp-db-board-card${isDragging ? " is-dragging" : ""}`} aria-label={row.name}
  >
    <div className="sp-db-board-card-heading">
      <button type="button" className="sp-db-board-card-title" ref={setActivatorNodeRef}
        {...(canWrite ? attributes : {})} {...listeners} data-db-focus={`drag:${row.id}`}
        onPointerDown={() => { held.current = false; }}
        onKeyDown={event => {
          if (event.code === "Enter") held.current = false;
          listeners?.onKeyDown?.(event);
        }}
        onClick={event => {
          if (held.current) { event.preventDefault(); return; }
          open(row);
        }} title={row.name}>
        <span className="sp-db-board-card-name">{row.name}</span>
      </button>
    </div>
    {properties.length > 0 && <dl>{properties.map(property => <div key={property.id}>
      <dt className="sr-only">{property.name}</dt>
      <dd title={`${property.name}: ${String(row.values[property.id])}`}><RecordValue field={property} value={row.values[property.id]} /></dd>
    </div>)}</dl>}
  </article>;
}

function BoardColumn({ group, label, field, count, hidden, showMore, canWrite, add, drafting, closeDraft, create, children }: {
  group: Group;
  label: string;
  field: DatabaseField;
  count: number;
  hidden: number;
  showMore: () => void;
  canWrite: boolean;
  add: () => void;
  drafting: boolean;
  closeDraft: () => void;
  create: (id: string, name: string) => Promise<void>;
  children: ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: targetId(group), disabled: !canWrite });
  return <section ref={setNodeRef} data-group={group ?? ""} tabIndex={-1}
    className={`sp-db-board-column${isOver ? " is-drop-target" : ""}`} aria-label={`${label} records`}>
    <div className="sp-db-board-heading">
      <h2><i aria-hidden="true" style={{ backgroundColor: optionColor(field, group) }} />
        <span className="sp-db-board-group-name" title={label}>{label}</span><span className="sp-db-board-count">{count}</span>
      </h2>
      <button className="sp-icon-button" aria-label={`Add record to ${group ?? "ungrouped"}`} disabled={!canWrite} onClick={add}>
        <Icon name="plus" size={14} />
      </button>
    </div>
    <div className="sp-db-board-stack">
      {children}
      {hidden > 0 && <button type="button" className="sp-db-board-more" onClick={showMore}
        title={`${hidden} more ${hidden === 1 ? "record" : "records"} in ${label}`}>
        <Icon name="down" size={14} />Load {Math.min(COLUMN_PAGE, hidden)} more
        <span className="sp-db-board-more-count">{hidden} hidden</span>
      </button>}
      {drafting && canWrite && <div className="sp-db-board-draft"><InlineRecordDraft label={`New record name in ${label}`} create={create} close={closeDraft} /></div>}
      {!count && !drafting && canWrite && <p className="sp-db-drop-hint">Drop a record here</p>}
    </div>
    {isOver && <span className="sr-only">Move here</span>}
    {!drafting && <button className="sp-db-board-add" disabled={!canWrite} onClick={add}><Icon name="plus" size={14} />New record</button>}
  </section>;
}

export function DatabaseBoard({ database, field, rows, pageKey, visibleFields, canWrite, open, draft, setDraft, create, move }: {
  database: WorkspaceDatabase;
  field: DatabaseField;
  rows: readonly DatabaseRecord[];
  pageKey: string;
  visibleFields: DatabaseField[];
  canWrite: boolean;
  open: (record: DatabaseRecord) => void;
  draft: { group: Group } | null;
  setDraft: (draft: { group: Group } | null) => void;
  create: (id: string, name: string, group: Group) => Promise<void>;
  move: (record: DatabaseRecord, group: Group) => Promise<void>;
}) {
  const [active, setActive] = useState<DatabaseRecord | null>(null);
  const board = useRef<HTMLDivElement>(null);
  // Per-column card limits reset when search, filter, or sort change. Records moved or
  // created here stay rendered even past the limit, so focus and feedback never vanish.
  const [shown, setShown] = useState<{ key: string; limits: Record<string, number>; revealed: string[] }>({ key: pageKey, limits: {}, revealed: [] });
  const page = shown.key === pageKey ? shown : { key: pageKey, limits: {}, revealed: [] };
  const limitOf = (group: Group) => page.limits[targetId(group)] ?? COLUMN_PAGE;
  const reveal = (id: string) => setShown(current => {
    const base = current.key === pageKey ? current : { key: pageKey, limits: {}, revealed: [] };
    return base.revealed.includes(id) ? base : { ...base, revealed: [...base.revealed, id] };
  });
  const showMore = (group: Group) => setShown(current => {
    const base = current.key === pageKey ? current : { key: pageKey, limits: {}, revealed: [] };
    const limit = base.limits[targetId(group)] ?? COLUMN_PAGE;
    return { ...base, limits: { ...base.limits, [targetId(group)]: limit + COLUMN_PAGE } };
  });
  const groups: Group[] = [null, ...(field.options ?? [])];
  const groupName = (group: Group) => group ?? `No ${field.name.toLowerCase()}`;
  const destination = (id: unknown) => groups.find(group => targetId(group) === id);
  const sensors = useSensors(
    // A mouse drag starts once the pointer moves, so a click of any length opens the card.
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    // Touch waits for a short hold so swiping still scrolls the board.
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: groupCoordinates(groups), scrollBehavior: "auto",
      keyboardCodes: { start: ["Space"], cancel: ["Escape"], end: ["Space"] },
    }),
  );
  function moveRow(row: DatabaseRecord, group: Group, fromKeyboard = false) {
    if (!canWrite || (row.values[field.id] || null) === group) return;
    reveal(row.id);
    const restoreFocus = () => {
      if (!fromKeyboard) return;
      requestAnimationFrame(() => {
        if (!board.current || document.activeElement !== document.body) return;
        const target = board.current.querySelector<HTMLElement>(`[data-db-focus="${CSS.escape(`drag:${row.id}`)}"]`)
          ?? board.current.querySelector<HTMLElement>(`[data-group="${CSS.escape(group ?? "")}"]`);
        target?.focus({ preventScroll: true });
      });
    };
    const saved = move(row, group);
    restoreFocus();
    void saved.then(restoreFocus, restoreFocus);
  }
  return <DndContext
    id={`space-database-board-${database.id}`} sensors={sensors}
    collisionDetection={args => args.pointerCoordinates ? pointerWithin(args) : rectIntersection(args)}
    accessibility={{
      screenReaderInstructions: {
        draggable: "Press Enter to open a record. Press Space to pick up a record. Use left and right arrows to choose a group. Press Space to drop, or Escape to cancel. You can also open the record to change its group.",
      },
      announcements: {
        onDragStart: ({ active }) => `Picked up ${active.data.current?.record?.name ?? "record"}.`,
        onDragOver: ({ over }) => {
          const group = destination(over?.id);
          return group !== undefined ? `Move to ${groupName(group)}.` : "Outside the board. Drop here to cancel.";
        },
        onDragEnd: ({ active, over }) => {
          const group = destination(over?.id);
          if (group === undefined) return "Move cancelled.";
          return active.data.current?.groupTarget === over?.id ? "Record is already in this group." : `Dropped in ${groupName(group)}. Saving move.`;
        },
        onDragCancel: () => "Move cancelled.",
      },
    }}
    onDragStart={({ active }) => setActive(rows.find(row => row.id === active.id) ?? null)}
    onDragCancel={() => setActive(null)}
    onDragEnd={({ active, over, activatorEvent }) => {
      const row = rows.find(row => row.id === active.id);
      const group = destination(over?.id);
      if (row && group !== undefined) moveRow(row, group, activatorEvent instanceof KeyboardEvent);
      setActive(null);
    }}
  >
    <div ref={board} className="sp-db-board" aria-label={`Records grouped by ${field.name}`}>
      {groups.map(group => {
        const items = rows.filter(row => (row.values[field.id] || null) === group);
        const limit = limitOf(group);
        const visible = items.filter((row, index) => index < limit || page.revealed.includes(row.id));
        return <BoardColumn key={targetId(group)} group={group} label={groupName(group)} field={field} count={items.length}
          hidden={items.length - visible.length} showMore={() => showMore(group)}
          canWrite={canWrite} add={() => setDraft({ group })} drafting={draft !== null && draft.group === group}
          closeDraft={() => setDraft(null)} create={(id, name) => { reveal(id); return create(id, name, group); }}>
          {visible.map(row => <BoardCard key={row.id} visibleFields={visibleFields} field={field} row={row}
            canWrite={canWrite} open={open} />)}
        </BoardColumn>;
      })}
    </div>
    <DragOverlay dropAnimation={null}>
      {active && <div className="sp-db-drag-preview"><span>{active.name}</span></div>}
    </DragOverlay>
  </DndContext>;
}
