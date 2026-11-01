import {
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
export function Button({
  variant = "default",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "danger" | "text";
}) {
  return (
    <button
      type={type}
      className={`button ${variant} ${className}`}
      {...props}
    />
  );
}
const icons = {
  apply:
    "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm-1.5-5.5L7 11l-1.4 1.4 4.9 4.9 8-8L17.1 7.9l-6.6 6.6z",
  add: "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
  search:
    "M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.71.71l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1 1 9.5 5a4.5 4.5 0 0 1 0 9z",
  history:
    "M13 3a9 9 0 0 0-9 9H1l4 4 4-4H6a7 7 0 1 1 2.05 4.95l-1.42 1.42A9 9 0 1 0 13 3zm-1 5v5l4.25 2.52.75-1.23-3.5-2.08V8H12z",
  package:
    "M12 2 2 7v10l10 5 10-5V7L12 2zm0 2.24L18.53 7 12 9.76 5.47 7 12 4.24zM4 8.62l7 3.5v7.64l-7-3.5V8.62zm9 11.14v-7.64l7-3.5v7.64l-7 3.5z",
  updateAll:
    "M12 2 2 7l10 5 10-5-10-5zm0 2.24L17.53 7 12 9.76 6.47 7 12 4.24zM3 10l-1 2 10 5 10-5-1-2-9 4.5L3 10zm0 5-1 2 10 5 10-5-1-2-9 4.5L3 15z",
  refresh:
    "M17.65 6.35A7.95 7.95 0 0 0 12 4a8 8 0 1 0 7.75 10h-2.1A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z",
  installation: "M9 3h6v8h4l-7 7-7-7h4V3zM5 19h14v2H5v-2z",
  live: "M3 3h18v18H3V3zm3 4v2h12V7H6zm0 4v2h8v-2H6zm0 4v2h12v-2H6z",
  test: "M8 5v14l11-7z",
  edit: "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
  delete:
    "M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
};
export function IconButton({
  icon,
  label,
  showLabel = false,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  icon: keyof typeof icons;
  label: string;
  showLabel?: boolean;
}) {
  return (
    <Button
      variant={icon === "delete" ? "danger" : "default"}
      className={`icon-button${showLabel ? " with-label" : ""}`}
      aria-label={label}
      title={label}
      {...props}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d={icons[icon]} />
      </svg>
      {showLabel && <span>{label}</span>}
    </Button>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}
export function Input({
  label,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <Field label={label} hint={hint}>
      <input {...props} />
    </Field>
  );
}
export function Select({
  label,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <Field label={label}>
      <select {...props}>{children}</select>
    </Field>
  );
}
export function Textarea({
  label,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string }) {
  return (
    <Field label={label}>
      <textarea {...props} />
    </Field>
  );
}
export function Checkbox({
  label,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="check">
      <input type="checkbox" {...props} />
      <span>{label}</span>
    </label>
  );
}
export function Card({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <article className="card">
      <div className="heading">
        <h2>{title}</h2>
        {actions && <div className="actions">{actions}</div>}
      </div>
      {children}
    </article>
  );
}
export function Notice({
  children,
  error = false,
}: {
  children?: ReactNode;
  error?: boolean;
}) {
  return children ? (
    <p
      className={`notice${error ? " error" : ""}`}
      role={error ? "alert" : "status"}
    >
      {children}
    </p>
  ) : null;
}
export function DataTable({
  columns,
  children,
  label,
}: {
  columns: string[];
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="table-wrap">
      <table aria-label={label}>
        <thead>
          <tr>
            {columns.map((name, i) => (
              <th
                scope="col"
                key={name}
                className={i === columns.length - 1 ? "row-actions" : ""}
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
export function Status({
  name,
  status,
  detail,
  children,
  tone = "pending",
}: {
  name: string;
  status: string;
  detail?: string;
  children?: ReactNode;
  tone?: "pending" | "success" | "error";
}) {
  return (
    <div className="connection-summary" data-state={tone}>
      <span className="connection-light" aria-hidden="true" />
      <div>
        <p className="connection-name">{name}</p>
        <p role="status">{status}</p>
        {detail && <p className="small muted">{detail}</p>}
      </div>
      {children}
    </div>
  );
}
export function LogView({
  children,
  label,
  live = false,
}: {
  children: string;
  label: string;
  live?: boolean;
}) {
  const ref = useRef<HTMLPreElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    if (live && follow.current && ref.current)
      ref.current.scrollTop = ref.current.scrollHeight;
  }, [children, live]);
  return (
    <pre
      ref={ref}
      className={`log-view${live ? " live-log" : ""}`}
      tabIndex={0}
      aria-label={label}
      onScroll={(e) => {
        const el = e.currentTarget;
        follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
    >
      {children}
    </pre>
  );
}
export function SidePanel({
  title,
  children,
  footer,
  onClose,
  busy = false,
  logs = false,
}: {
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  busy?: boolean;
  logs?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    const previous = document.activeElement;
    const root = document.documentElement;
    const wasLocked = root.classList.contains("has-side-panel");
    root.classList.add("has-side-panel");
    dialog.showModal();
    return () => {
      dialog.close();
      if (!wasLocked) root.classList.remove("has-side-panel");
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="side-panel"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="panel-head">
        <h2 id={titleId}>{title}</h2>
        <Button aria-label={`Close ${title}`} disabled={busy} onClick={onClose}>
          ×
        </Button>
      </div>
      <div
        className={`panel-body${logs ? " panel-body--logs" : ""}`}
        aria-busy={busy}
      >
        {children}
      </div>
      <footer className="panel-footer">{footer}</footer>
    </dialog>
  );
}
export function JsonBlock({ value, label }: { value: unknown; label: string }) {
  const source = JSON.stringify(value, null, 2) ?? "null";
  const tokens = source.split(
    /("(?:\\.|[^"\\])*"\s*:|"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[{}\[\],:])/g,
  );
  return (
    <div className="json-block">
      <div className="code-language" aria-hidden="true">
        JSON
      </div>
      <pre tabIndex={0} aria-label={label}>
        <code>
          {tokens.map((text, i) => (
            <span
              key={i}
              className={
                text.startsWith('"')
                  ? text.endsWith(":")
                    ? "json-key"
                    : "json-string"
                  : /^(true|false|null)$/.test(text)
                    ? "json-literal"
                    : /^-?\d/.test(text)
                      ? "json-number"
                      : "json-punctuation"
              }
            >
              {text}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
