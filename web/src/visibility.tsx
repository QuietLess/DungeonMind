// Stub (M7 RED).
import type { Board, Role, Visibility } from "./types";
export function visibilityOptions(_board: Board, _role: Role): Visibility[] {
  return [];
}
export function VisibilityBadge(_props: { visibility: Visibility }) {
  return null;
}
export function VisibilityPicker(_props: {
  board: Board;
  role: Role;
  value: Visibility;
  onChange: (v: Visibility) => void;
}) {
  return null;
}
