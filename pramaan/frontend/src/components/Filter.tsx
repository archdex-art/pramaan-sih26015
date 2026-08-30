/**
 * A labelled select, in the register's shape.
 *
 * Extracted from `screens/Register.tsx`, where it was a local component. Once a
 * second screen needed a filter the local copy became a fork waiting to happen:
 * the audit trail grew its own bare `<label>` + `<select>` and immediately
 * looked different — a stacked label, a full-width control, and a button beside
 * it that had inherited the rail's block layout. One filter control means one
 * appearance, and a screen cannot drift from it by not knowing it exists.
 *
 * The label is visible, not a placeholder. A placeholder disappears the moment
 * the control has a value, which is exactly when a reader scanning a row of
 * filters needs to know which one they are looking at.
 */

export function Filter({
  name,
  value,
  onChange,
  options,
}: {
  name: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="filter">
      <span className="label">{name}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}
