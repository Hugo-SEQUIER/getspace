import { useEffect, useState } from "react";
import type { DatabaseField, WorkspaceDatabase } from "../../../lib/space/databases";
import { useSpace } from "./provider";
import { Modal } from "./ui";
import { Icon } from "./ui-icon";
import { useFeedback } from "../notifications";

export function useDatabaseBoardProperties(database: WorkspaceDatabase | undefined) {
  const data = useSpace();
  const storageKey = database ? `space-board-properties-v1:${JSON.stringify([
    data.accountId, data.space?.id, database.id,
  ])}` : "";
  const [selection, setSelection] = useState<{ key: string; ids: string[] }>({ key: "", ids: [] });
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  useEffect(() => {
    let ids: string[] = [];
    try {
      const saved: unknown = JSON.parse((storageKey && localStorage.getItem(storageKey)) || "[]");
      if (Array.isArray(saved)) ids = [...new Set(saved.filter((id): id is string => typeof id === "string" && id.length <= 200))].slice(0, 30);
    } catch {}
    setSelection({ key: storageKey, ids });
    setStorageUnavailable(false);
  }, [storageKey]);
  const selectedIds = selection.key === storageKey ? selection.ids : [];
  function showProperties(ids: string[]) {
    if (!database) return;
    const next = [...new Set(ids)].filter(id => database.fields.some(property => property.id === id));
    setSelection({ key: storageKey, ids: next });
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
      setStorageUnavailable(false);
    } catch { setStorageUnavailable(true); }
  }
  return { selectedIds, showProperties, storageUnavailable };
}

export function DatabaseBoardProperties({ database, field, selectedIds, showProperties, storageUnavailable }: {
  database: WorkspaceDatabase;
  field: DatabaseField;
  selectedIds: readonly string[];
  showProperties: (ids: string[]) => void;
  storageUnavailable: boolean;
}) {
  const [choosingProperties, setChoosingProperties] = useState(false);
  useFeedback({ error: storageUnavailable ? "Browser storage is unavailable. These choices apply while this board stays open." : "" });
  const availableFields = database.fields.filter(property => property.id !== field.id);
  const visibleFields = availableFields.filter(property => selectedIds.includes(property.id));
  return <>
    <button type="button" aria-haspopup="dialog" aria-label="Card properties"
      title={visibleFields.length ? `${visibleFields.length} properties shown` : "Title only"}
      onClick={() => setChoosingProperties(true)}>
      <Icon name="sliders" size={16} />Card properties
      <span className="sp-db-property-count" aria-hidden="true">{visibleFields.length}</span>
    </button>
    {choosingProperties && <Modal title="Card properties" onClose={() => setChoosingProperties(false)} className="sp-db-card-properties-modal">
      <div className="sp-modal-content">
        <p className="sp-muted">Names are always visible. Choose any extra properties to show on cards. {field.name} is already shown in the column headings.</p>
        <fieldset className="sp-db-card-property-options">
          <legend className="sr-only">Properties shown on cards</legend>
          {availableFields.map(property => <label key={property.id}>
            <input type="checkbox" checked={selectedIds.includes(property.id)} onChange={event => showProperties(
              event.target.checked ? [...selectedIds, property.id] : selectedIds.filter(id => id !== property.id),
            )} />
            <span>{property.name}</span>
          </label>)}
          {!availableFields.length && <p>No extra properties are available.</p>}
        </fieldset>
        {!storageUnavailable && <p className="sp-muted">Saved in this browser for this database. Records are not changed.</p>}
        <div className="sp-modal-footer">
          <button type="button" className="sp-text-button" disabled={!visibleFields.length} onClick={() => showProperties([])}>Hide all</button>
          <button type="button" className="sp-primary" onClick={() => setChoosingProperties(false)}>Done</button>
        </div>
      </div>
    </Modal>}
  </>;
}
