import { useEffect, useId, useState } from "react";

interface Option {
  id: number;
  name: string;
}

interface CreatableSelectProps {
  options: Option[];
  value: string;
  onChange: (name: string) => void;
  onResolved: (id: number | null) => void;
  placeholder?: string;
  className?: string;
}

/**
 * A plain text input backed by a <datalist> of existing options. Typing an existing
 * name resolves to its id on blur; typing a new name leaves the id null so the caller
 * can find-or-create it on save (keeps this component free of API/mutation concerns).
 */
export function CreatableSelect({ options, value, onChange, onResolved, placeholder, className }: CreatableSelectProps) {
  const listId = useId();
  const [text, setText] = useState(value);

  useEffect(() => setText(value), [value]);

  const resolve = (name: string) => {
    const trimmed = name.trim();
    const match = options.find((o) => o.name.toLowerCase() === trimmed.toLowerCase());
    onChange(trimmed);
    onResolved(match ? match.id : null);
  };

  return (
    <>
      <input
        list={listId}
        value={text}
        placeholder={placeholder}
        className={className}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => resolve(e.target.value)}
      />
      <datalist id={listId}>
        {options.map((o) => (
          <option key={o.id} value={o.name} />
        ))}
      </datalist>
    </>
  );
}
