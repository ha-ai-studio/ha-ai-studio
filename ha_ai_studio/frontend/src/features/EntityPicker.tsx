import { useEffect, useId, useState } from "react";
import { Button, Input, Select, SidePanel } from "../design-system";
import { appUrl } from "../lib/api";
export interface HAEntity {
  entity_id: string;
  name: string;
  state: string;
  available: boolean;
}
export interface EntityPickerOptions {
  domains: ("camera" | "light" | "switch" | "sensor")[];
  title?: string;
}
export function EntitySearchField({label, hint, value, onChoose, onClear}: {
  label: string; hint?: string; value: string; onChoose: () => void; onClear: () => void;
}) {
  const id = useId();
  return <div className="field">
    <label htmlFor={id}>{label}</label>
    <div className="entity-field-control">
      <input id={id} value={value} readOnly placeholder="Choose an entity"
        aria-describedby={hint ? `${id}-hint` : undefined}
        onClick={onChoose} onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {event.preventDefault(); onChoose();}
        }} />
      <button type="button" className="entity-field-search" aria-label={`Choose ${label}`}
        title={`Choose ${label}`} onClick={onChoose}>
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>
        </svg>
      </button>
      <button type="button" className="entity-field-clear" aria-label={`Clear ${label}`}
        title={`Clear ${label}`} disabled={!value} onClick={onClear}>×</button>
    </div>
    {hint && <small id={`${id}-hint`} className="muted">{hint}</small>}
  </div>;
}

export function EntityPicker({
  options,
  onSelect,
  onClose,
}: {
  options: EntityPickerOptions;
  onSelect: (entity: HAEntity) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState("");
  const [items, setItems] = useState<HAEntity[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setLoading(true);
    setError("");
    setItems([]);
    const timer = setTimeout(async () => {
      try {
        const url = appUrl("api/ha-entities");
        url.searchParams.set("domains", domain || options.domains.join(","));
        url.searchParams.set("q", query);
        const response = await fetch(url, { signal: controller.signal });
        const data = await response.json();
        if (!response.ok)
          throw new Error(data.error || "Entity search failed.");
        if (alive) {
          setItems(data.entities);
          setTotal(data.total);
        }
      } catch (reason) {
        if (alive)
          setError(
            reason instanceof Error ? reason.message : "Entity search failed.",
          );
      } finally {
        if (alive) setLoading(false);
      }
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, domain, options, retry]);
  return (
    <SidePanel
      title={options.title || "Select Home Assistant entity"}
      onClose={onClose}
      footer={<Button onClick={onClose}>Cancel</Button>}
    >
      <div className="entity-search-controls">
        <div className="entity-search-input">
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>
        <Input
          autoFocus
          label="Search entities"
          placeholder="Name or entity ID"
          value={query}
          maxLength={120}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.preventDefault();
          }}
          onChange={(e) => setQuery(e.target.value)}
        />
        </div>
        {options.domains.length > 1 && (
          <Select
            label="Entity type"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
          >
            <option value="">All supported types</option>
            {options.domains.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </Select>
        )}
      </div>
      <p className="entity-search-hint">Select an entity to use in this Action. Devices remain unchanged.</p>
      <div className="entity-search-summary" role="status">
        {loading
          ? "Searching…"
          : error
            ? ""
            : `${total} matching entities${total > 100 ? " — showing 100; refine your search" : ""}`}
      </div>
      {error && (
        <div className="entity-search-error" role="alert">
          <p>{error}</p>
          <Button onClick={() => setRetry((v) => v + 1)}>Retry</Button>
        </div>
      )}
      {!loading && !error && !items.length && (
        <p className="entity-search-empty">No matching entities. Try a different name or entity ID.</p>
      )}
      <ul className="entity-search-results">
        {items.map((entity) => (
          <li key={entity.entity_id}>
            <Button
              aria-label={`Select ${entity.name} (${entity.entity_id}), ${entity.available ? entity.state : "Unavailable"}`}
              disabled={!entity.available || loading}
              onClick={() => onSelect(entity)}
            >
              <span className="entity-search-name">{entity.name}</span>
              <span className="muted">{entity.entity_id}</span>
              <span
                className={
                  entity.available ? "entity-available" : "entity-unavailable"
                }
              >
                {entity.available ? entity.state : "Unavailable"}
              </span>
            </Button>
          </li>
        ))}
      </ul>
    </SidePanel>
  );
}
