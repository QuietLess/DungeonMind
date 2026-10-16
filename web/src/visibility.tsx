// Who can see an entry, always shown as icon + text (M7 UI6), never colour alone.
import { useId } from "react";
import { CrownIcon, LockIcon, UsersIcon } from "./icons";
import type { Board, Role, Visibility } from "./types";

export const VISIBILITY_LABEL: Record<Visibility, string> = {
  private: "Private",
  dm: "Shared with DM",
  party: "Party",
};

const HINT: Record<Visibility, string> = {
  private: "Only you",
  dm: "You and the DM",
  party: "Everyone in the campaign",
};

const ICON = { private: LockIcon, dm: CrownIcon, party: UsersIcon };

/** The DM already reads their own private entries, and lore is either secret or revealed. */
export function visibilityOptions(board: Board, role: Role): Visibility[] {
  return role === "dm" || board === "lore" ? ["private", "party"] : ["private", "dm", "party"];
}

export function VisibilityBadge({ visibility }: { visibility: Visibility }) {
  const Glyph = ICON[visibility];
  return (
    <span className={`badge badge-${visibility}`}>
      <Glyph />
      <span>{VISIBILITY_LABEL[visibility]}</span>
    </span>
  );
}

export function VisibilityPicker({
  board,
  role,
  value,
  onChange,
}: {
  board: Board;
  role: Role;
  value: Visibility;
  onChange: (v: Visibility) => void;
}) {
  const name = useId();
  const labelId = useId();
  const options = visibilityOptions(board, role);
  return (
    <div role="radiogroup" aria-labelledby={labelId} className="visibility-picker">
      <span id={labelId} className="field-label">
        Who can see this?
      </span>
      <div className="segmented">
        {options.map((v) => {
          const Glyph = ICON[v];
          return (
            <label key={v} className={value === v ? "selected" : undefined}>
              <input
                type="radio"
                name={name}
                value={v}
                checked={value === v}
                onChange={() => onChange(v)}
              />
              <Glyph />
              <span>{VISIBILITY_LABEL[v]}</span>
              <small>{v === "private" && role === "dm" && board === "lore" ? "Secret" : HINT[v]}</small>
            </label>
          );
        })}
      </div>
    </div>
  );
}
