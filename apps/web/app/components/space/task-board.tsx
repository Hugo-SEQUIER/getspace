
import Link from "@web/lib/navigation";
import { useEffect, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  pointerWithin,
  rectIntersection,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type KeyboardCoordinateGetter,
} from "@dnd-kit/core";
import {
  type TaskColumn,
  type Project,
  type Resource,
  type Status,
} from "../../../lib/space/types";
import type { PlannedRow } from "../../../lib/space/today";
import { Icon } from "./ui-icon";
import { StatusDot } from "./ui";
import "./task-board-refresh.css";

function keyboardCoordinates(columns: TaskColumn[]): KeyboardCoordinateGetter {
  return (event, { context, currentCoordinates }) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.code)) return;
    event.preventDefault();
    const current = columns.findIndex(column =>
      column.value === (context.over?.id ?? context.active?.data.current?.status),
    );
    const next = columns[Math.max(0, Math.min(columns.length - 1,
      current + (event.code === "ArrowRight" ? 1 : -1)))];
    const target = next && context.droppableRects.get(next.value);
    const rect = context.collisionRect;
    if (!target || !rect) return;
    return {
      x: currentCoordinates.x + target.left + target.width / 2 - (rect.left + rect.width / 2),
      y: currentCoordinates.y + target.top + target.height / 2 - (rect.top + rect.height / 2),
    };
  };
}

type Selection = { ids: ReadonlySet<string>; toggle: (id: string) => void };

/** A task database row planned for today, placed in the board column its status names. */
export type BoardRow = { key: string; row: PlannedRow; column: Status };
const ROW_DRAG = "row:";

function shortDay(day: string) {
  return new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function RowCard({
  item,
  label,
  base,
  today,
  disabled,
  saving,
  selecting,
  unplan,
}: {
  item: BoardRow;
  label: string;
  base: string;
  today: string;
  disabled: boolean;
  saving: boolean;
  selecting: boolean;
  unplan: (row: PlannedRow) => void;
}) {
  const { row } = item;
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } =
    useDraggable({ id: ROW_DRAG + item.key, data: { status: item.column }, disabled: disabled || selecting, attributes: { role: "link" } });
  return (
    <article
      ref={setNodeRef}
      className={`sp-board-task sp-board-row ${isDragging ? "is-dragging" : ""}`}
      aria-label={row.record.name}
      aria-busy={saving}
    >
      <Link
        href={`${base}/databases/${row.database.id}`}
        className="sp-board-task-link"
        draggable={false}
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        aria-label={`${row.record.name}, ${row.database.name}`}
        aria-disabled={undefined}
        aria-roledescription={disabled || selecting ? undefined : "draggable database row"}
        title={disabled ? undefined : "Click to open its database, or drag to change its status."}
      >
        <span className="sp-board-task-heading">{row.record.name}</span>
        <span className="sp-task-project">
          <Icon name="database" size={14} />
          <span>{row.database.name}</span>
        </span>
        {(row.planned !== today || (row.status && row.status.toLowerCase() !== label.toLowerCase())) && (
          <span className="sp-board-row-meta">
            {row.status && row.status.toLowerCase() !== label.toLowerCase() && <span>{row.status}</span>}
            {row.planned && row.planned !== today && <span>Planned {shortDay(row.planned)}</span>}
          </span>
        )}
        {saving && <span className="sp-task-save-state sr-only" role="status">Saving row…</span>}
      </Link>
      {!selecting && (
        <button
          type="button"
          className="sp-board-row-unplan"
          aria-label={`Remove ${row.record.name} from today`}
          title="Remove from today"
          disabled={disabled || saving}
          onClick={() => unplan(row)}
        >
          <Icon name="close" size={13} />
        </button>
      )}
    </article>
  );
}

function TaskCardContent({ task, project, saving }: { task: Resource; project?: string; saving: boolean }) {
  return (
    <>
      <span className="sp-board-task-heading">{task.title}</span>
      <span className="sp-task-project">
        <Icon name="folder" size={14} />
        <span>{project ?? (task.project ? "Project unavailable" : "No project")}</span>
      </span>
      {task.priority && (
        <span className="sp-board-task-footer">
          <span className="sp-priority" data-priority={task.priority.toLowerCase()}>
            <Icon name="flag" size={14} />
            {task.priority}
          </span>
        </span>
      )}
      {saving && <span className="sp-task-save-state sr-only" role="status">Saving status…</span>}
    </>
  );
}

function TaskCard({
  task,
  base,
  project,
  selected,
  disabled,
  saving,
  selection,
}: {
  task: Resource;
  base: string;
  project?: string;
  selected: boolean;
  disabled: boolean;
  saving: boolean;
  selection?: Selection;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } =
    useDraggable({ id: task.id, data: { status: task.status }, disabled: disabled || !!selection, attributes: { role: "link" } });
  useEffect(() => {
    if (!isDragging) return;
    const preventNavigation = (event: MouseEvent) => event.preventDefault();
    document.addEventListener("click", preventNavigation, true);
    return () => {
      window.setTimeout(() => document.removeEventListener("click", preventNavigation, true), 50);
    };
  }, [isDragging]);
  if (selection) {
    const checked = selection.ids.has(task.id);
    return (
      <article
        ref={setNodeRef}
        className={`sp-board-task is-selectable ${checked ? "is-checked" : ""}`}
        aria-label={task.title}
        aria-busy={saving}
      >
        <label className="sp-board-task-link sp-board-task-select">
          <input
            type="checkbox"
            className="sp-task-checkbox"
            checked={checked}
            onChange={() => selection.toggle(task.id)}
            aria-label={`Select ${task.title}`}
          />
          <TaskCardContent task={task} project={project} saving={saving} />
        </label>
      </article>
    );
  }
  return (
    <article
      ref={setNodeRef}
      className={`sp-board-task ${selected ? "selected" : ""} ${isDragging ? "is-dragging" : ""}`}
      aria-label={task.title}
      aria-busy={saving}
    >
      <Link
        href={`${base}/tasks/${task.id}?view=board`}
        className="sp-board-task-link"
        draggable={false}
        data-task-focus={`drag-${task.id}`}
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        aria-label={task.title}
        aria-disabled={undefined}
        aria-roledescription={disabled ? undefined : "draggable task link"}
        aria-describedby={disabled ? undefined : attributes["aria-describedby"]}
        title={disabled ? undefined : "Click to open, or drag to change status."}
      >
        <TaskCardContent task={task} project={project} saving={saving} />
      </Link>
    </article>
  );
}

function Column({
  status,
  tasks,
  rows,
  rowSaving,
  today,
  unplanRow,
  base,
  projects,
  selectedId,
  disabled,
  pending,
  selection,
  add,
}: {
  status: TaskColumn;
  tasks: Resource[];
  rows: BoardRow[];
  rowSaving: ReadonlySet<string>;
  today: string;
  unplanRow: (row: PlannedRow) => void;
  base: string;
  projects: Project[];
  selectedId?: string;
  disabled: boolean;
  pending: Readonly<Record<string, Status>>;
  selection?: Selection;
  add: (status: Status) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: status.value, disabled });
  return (
    <section
      ref={setNodeRef}
      data-status={status.value}
      className={`sp-task-group ${isOver ? "is-drop-target" : ""}`}
      aria-label={`${status.label} tasks`}
    >
      <h2>
        <StatusDot status={status.value} />
        <span className="sp-task-column-label">{status.label}</span>
        <span className="sp-count">{tasks.length + rows.length}</span>
      </h2>
      <div className="sp-board-task-stack">
        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            base={base}
            selected={selectedId === task.id}
            project={projects.find((p) => p.id === task.project)?.name}
            disabled={disabled}
            saving={Object.hasOwn(pending, task.id)}
            selection={selection}
          />
        ))}
        {rows.map((item) => (
          <RowCard
            key={item.key}
            item={item}
            label={status.label}
            base={base}
            today={today}
            disabled={disabled}
            saving={rowSaving.has(item.key)}
            selecting={!!selection}
            unplan={unplanRow}
          />
        ))}
        {!tasks.length && !rows.length && (
          <div className="sp-task-drop-hint">Drop a task here</div>
        )}
      </div>
      <button className="sp-add-task" disabled={disabled} onClick={() => add(status.value)}>
        <Icon name="plus" size={14} />
        Add task
      </button>
    </section>
  );
}

export function TaskBoard({
  tasks,
  rows = [],
  rowSaving = new Set(),
  today = "",
  moveRow = () => {},
  unplanRow = () => {},
  base,
  projects,
  selectedId,
  disabled,
  pending,
  columns,
  selection,
  changeStatus,
  add,
}: {
  tasks: Resource[];
  /** Database rows planned for today, shown beside the native tasks. */
  rows?: BoardRow[];
  rowSaving?: ReadonlySet<string>;
  today?: string;
  moveRow?: (row: PlannedRow, column: TaskColumn) => void;
  unplanRow?: (row: PlannedRow) => void;
  base: string;
  projects: Project[];
  selectedId?: string;
  disabled: boolean;
  pending: Readonly<Record<string, Status>>;
  columns: TaskColumn[];
  /** When set, cards toggle selection instead of opening or dragging. */
  selection?: Selection;
  changeStatus: (task: Resource, status: Status) => void;
  add: (status: Status) => void;
}) {
  const [active, setActive] = useState<{ title: string; status: Status } | null>(null);
  // Task cards and row cards share the board; a row's drag ID carries its prefix.
  function item(id: unknown) {
    const key = String(id);
    if (key.startsWith(ROW_DRAG)) {
      const found = rows.find((row) => ROW_DRAG + row.key === key);
      return found && { title: found.row.record.name, status: found.column, row: found };
    }
    const task = tasks.find((task) => task.id === key);
    return task && { title: task.title, status: task.status, task };
  }
  const sensors = useSensors(
    // A mouse drag starts once the pointer moves, so a click of any length opens the task.
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    // Touch waits for a short hold so swiping still scrolls the board.
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: keyboardCoordinates(columns),
      keyboardCodes: { start: ["Space"], end: ["Space", "Tab"], cancel: ["Escape"] },
    }),
  );
  return (
    <DndContext
      id="space-task-board"
      sensors={sensors}
      collisionDetection={(args) =>
        args.pointerCoordinates ? pointerWithin(args) : rectIntersection(args)
      }
      accessibility={{
        screenReaderInstructions: {
          draggable:
            "Press Enter to open a task. Drag it, or press Space to pick up. Use left and right arrows to choose a status column. Press Space to drop, or Escape to cancel.",
        },
        announcements: {
          onDragStart: ({ active }) =>
            `Picked up ${item(active.id)?.title ?? "task"}.`,
          onDragOver: ({ over }) =>
            over
              ? `Over ${columns.find((status) => status.value === over.id)?.label}.`
              : "Outside the board. Drop here to cancel.",
          onDragEnd: ({ active, over }) => {
            const task = item(active.id);
            const column = columns.find(column => column.value === over?.id);
            if (!task || !column) return "Move cancelled.";
            return task.status === column.value
              ? `Still in ${column.label}. Status unchanged.`
              : `Dropped in ${column.label}. Saving status.`;
          },
          onDragCancel: () => "Move cancelled.",
        },
      }}
      onDragStart={({ active }) => {
        const found = item(active.id);
        setActive(found ? { title: found.title, status: found.status } : null);
      }}
      onDragCancel={() => setActive(null)}
      onDragEnd={({ active: dragged, over }) => {
        const status = columns.find(
          (status) => status.value === over?.id,
        )?.value;
        // A card that is still saving stays movable: the provider sends the latest move next.
        const moved = item(dragged.id);
        const column = columns.find((column) => column.value === status);
        if (moved && column && moved.status !== column.value && !disabled) {
          if ("task" in moved && moved.task) changeStatus(moved.task, column.value);
          if ("row" in moved && moved.row) moveRow(moved.row.row, column);
        }
        setActive(null);
      }}
    >
      <div className="sp-task-board">
        <div className="sp-board sp-draggable-board">
          {columns.map((status) => (
            <Column
              key={status.value}
              status={status}
              tasks={tasks.filter((task) => task.status === status.value)}
              rows={rows.filter((row) => row.column === status.value)}
              rowSaving={rowSaving}
              today={today}
              unplanRow={unplanRow}
              base={base}
              projects={projects}
              selectedId={selectedId}
              disabled={disabled}
              pending={pending}
              selection={selection}
              add={add}
            />
          ))}
        </div>
        <DragOverlay dropAnimation={null}>
          {active ? (
            <div className="sp-task-drag-preview">
              <StatusDot status={active.status} />
              {active.title}
            </div>
          ) : null}
        </DragOverlay>
      </div>
    </DndContext>
  );
}
