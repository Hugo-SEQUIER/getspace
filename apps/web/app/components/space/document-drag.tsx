
import { createContext, useContext, useRef, useState, type PointerEvent, type ReactNode } from "react";
import {
  DndContext, DragOverlay, KeyboardSensor, PointerSensor,
  pointerWithin, useDraggable, useDroppable, useSensor, useSensors,
  type KeyboardCoordinateGetter, type Modifier,
} from "@dnd-kit/core";
import { message } from "../../../lib/client";
import type { Resource } from "../../../lib/space/types";
import { useSpace } from "./provider";
import { Icon } from "./ui-icon";
import { useFeedback } from "../notifications";
import "./document-drag.css";

const DragState = createContext({ disabled: true, dragging: false, canWrite: false });
const targetId = (folder: string) => `document-folder:${folder || "unfiled"}`;
const keepPreviewInView: Modifier = ({ transform, overlayNodeRect, windowRect }) => {
  if (!overlayNodeRect || !windowRect) return transform;
  return {
    ...transform,
    x: Math.max(windowRect.left - overlayNodeRect.left,
      Math.min(transform.x, windowRect.right - overlayNodeRect.right)),
    y: Math.max(windowRect.top - overlayNodeRect.top,
      Math.min(transform.y, windowRect.bottom - overlayNodeRect.bottom)),
  };
};

const folderCoordinates: KeyboardCoordinateGetter = (event, { context, currentCoordinates }) => {
  if (!["ArrowUp", "ArrowDown"].includes(event.code)) return;
  event.preventDefault();
  const targets = context.droppableContainers.getEnabled()
    .flatMap(container => {
      const rect = context.droppableRects.get(container.id);
      return rect && rect.width > 0 && rect.height > 0 ? [{ id: container.id, rect }] : [];
    })
    .sort((a, b) => a.rect.top - b.rect.top);
  const direction = event.code === "ArrowDown" ? 1 : -1;
  const currentTarget = context.over?.id ?? context.active?.data.current?.folderTarget;
  let index = targets.findIndex(target => target.id === currentTarget);
  if (index < 0) index = direction > 0 ? 0 : targets.length - 1;
  else index = Math.max(0, Math.min(targets.length - 1, index + direction));
  const target = targets[index]?.rect;
  const rect = context.collisionRect;
  if (!target || !rect) return;
  return {
    x: currentCoordinates.x + target.left + target.width / 2 - (rect.left + rect.width / 2),
    y: currentCoordinates.y + target.top + target.height / 2 - (rect.top + rect.height / 2),
  };
};

export function DocumentDragProvider({
  children, disabled = false, onDragStart, onMoved,
}: {
  children: ReactNode;
  disabled?: boolean;
  onDragStart: () => void;
  onMoved: (document: Resource) => void;
}) {
  const data = useSpace();
  const [active, setActive] = useState<Resource | null>(null);
  const [moving, setMoving] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error, loading: moving ? "Moving document…" : null, clearError: () => setError("") });
  const saving = useRef(false);
  const unavailable = disabled || !data.canWrite || !data.folderReady || moving;
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: folderCoordinates }),
  );
  const folders = data.folders.filter(folder => !folder.deleted);
  const destinationName = (id: unknown) => id === targetId("")
    ? "Unfiled" : folders.find(folder => targetId(folder.id) === id)?.name;

  async function move(document: Resource, folder: string, focusTarget: boolean) {
    if (unavailable || saving.current || (document.folder ?? "") === folder) return;
    const destination = folder ? folders.find(item => item.id === folder) : undefined;
    if (folder && !destination) return;
    saving.current = true;
    setMoving(true);
    setError("");
    try {
      const latest = await data.readResource(document.id, undefined, true);
      if (latest.kind !== "document" || latest.recall?.category === "memory")
        throw new Error("Only documents can be moved to a folder.");
      if ((latest.folder ?? "") === folder) return;
      const saved = await data.saveResource({
        kind: "document", title: latest.title, body: latest.body,
        status: latest.status, folder,
      }, latest);
      onMoved(saved);
      data.setNotice(`Moved “${saved.title}” to ${destination ? destination.name : "Unfiled"}.`);
      if (focusTarget) requestAnimationFrame(() => {
        window.document.getElementById(targetId(folder))?.querySelector<HTMLAnchorElement>("a")?.focus();
      });
    } catch (e) {
      setError(`Could not move “${document.title}”. ${message(e)}`);
    } finally {
      saving.current = false;
      setMoving(false);
    }
  }

  return (
    <DragState.Provider value={{ disabled: unavailable, dragging: !!active, canWrite: data.canWrite }}>
      <DndContext
        id="space-document-folders"
        sensors={sensors}
        collisionDetection={args => pointerWithin({
          ...args,
          pointerCoordinates: args.pointerCoordinates ?? {
            x: args.collisionRect.left + args.collisionRect.width / 2,
            y: args.collisionRect.top + args.collisionRect.height / 2,
          },
        })}
        accessibility={{
          screenReaderInstructions: {
            draggable: "Press Space to pick up a document. Use up and down arrows to choose a folder or Unfiled. Press Space to drop, or Escape to cancel.",
          },
          announcements: {
            onDragStart: ({ active }) => `Picked up ${active.data.current?.document?.title ?? "document"}. Choose a folder or Unfiled.`,
            onDragOver: ({ over }) => {
              const name = destinationName(over?.id);
              return name ? `Move to ${name}.` : "Outside a folder. Drop here to cancel.";
            },
            onDragEnd: ({ active, over }) => {
              const name = destinationName(over?.id);
              if (!name) return "Move cancelled.";
              if (active.data.current?.folderTarget === over?.id)
                return "Document is already in this folder.";
              return `Dropped in ${name}. Saving move.`;
            },
            onDragCancel: () => "Move cancelled.",
          },
        }}
        onDragStart={({ active }) => {
          if (unavailable || saving.current) return;
          setError("");
          setActive(active.data.current?.document ?? null);
          onDragStart();
        }}
        onDragCancel={() => setActive(null)}
        onDragEnd={({ active, over, activatorEvent }) => {
          const document: Resource | undefined = active.data.current?.document;
          const folder = folders.find(folder => targetId(folder.id) === over?.id);
          if (document && (folder || over?.id === targetId("")))
            void move(document, folder?.id ?? "", activatorEvent instanceof KeyboardEvent);
          setActive(null);
        }}
      >
        {children}
        <DragOverlay dropAnimation={null} modifiers={[keepPreviewInView]} style={{ width: 280, maxWidth: "calc(100vw - 16px)" }}>
          {active && <div className="sp-doc-drag-preview"><Icon name="document" size={17} /><span>{active.title}</span></div>}
        </DragOverlay>
      </DndContext>
    </DragState.Provider>
  );
}

export function useDocumentDragState() {
  return useContext(DragState);
}

export function useDocumentDraggable(document: Resource, surface: string) {
  const { disabled, canWrite } = useDocumentDragState();
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } = useDraggable({
    id: `${surface}:${document.id}`,
    data: { document, folderTarget: targetId(document.folder ?? "") },
    disabled,
  });
  const handle = !canWrite ? null : (
    <button
      type="button"
      className="sp-doc-drag-handle"
      ref={setActivatorNodeRef}
      {...attributes}
      {...listeners}
      aria-label={`Drag ${document.title}`}
      title="Drag to a folder or Unfiled. Use Space and arrow keys with a keyboard."
      disabled={disabled}
    ><Icon name="grip" size={16} /></button>
  );
  const onPointerDown = (event: PointerEvent<HTMLElement>) => {
    if (event.target instanceof Element && !event.target.closest("button, input, select, textarea"))
      listeners?.onPointerDown?.(event);
  };
  return { setNodeRef, isDragging, onPointerDown, handle };
}

export function DocumentFolderDropTarget({ folder = "", className, children }: {
  folder?: string;
  className: string;
  children: ReactNode;
}) {
  const { disabled, dragging } = useDocumentDragState();
  const { setNodeRef, isOver } = useDroppable({ id: targetId(folder), disabled });
  return (
    <div
      ref={setNodeRef}
      id={targetId(folder)}
      data-folder-drop={folder || "unfiled"}
      className={`${className} ${dragging ? "sp-doc-drop-folder" : ""} ${isOver ? "is-drop-target" : ""}`}
    >{children}{isOver && <span className="sp-doc-drop-label">Move here</span>}</div>
  );
}
