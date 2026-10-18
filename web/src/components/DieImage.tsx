// Die pictures for the dice tray and the game log (M11 D3). Plain SVG, nothing remote.
const SHAPES: Record<number, string> = {
  4: "M50 6 L94 88 L6 88 Z",
  6: "M14 14 H86 V86 H14 Z",
  8: "M50 4 L94 50 L50 96 L6 50 Z",
  10: "M50 4 L92 40 L50 96 L8 40 Z",
  12: "M50 4 L93 35 L77 88 H23 L7 35 Z",
  20: "M50 3 L93 27 V73 L50 97 L7 73 V27 Z",
  100: "M50 4 L92 40 L50 96 L8 40 Z",
};

export function DieImage({ sides, size = 44, value }: { sides: number; size?: number; value?: number | string }) {
  const label = value ?? (sides === 100 ? "%" : sides);
  return (
    <svg className={`die die-d${sides}`} viewBox="0 0 100 100" width={size} height={size} aria-hidden="true" focusable="false">
      <path d={SHAPES[sides] ?? SHAPES[6]} className="die-face" />
      {sides === 20 && <path d="M50 3 L28 62 H72 Z M7 27 L28 62 M93 27 L72 62 M50 97 L28 62 M50 97 L72 62" className="die-edge" />}
      {sides === 10 || sides === 100 ? <path d="M8 40 L50 62 L92 40 M50 62 V96" className="die-edge" /> : null}
      <text x="50" y={sides === 4 ? 70 : 60} textAnchor="middle" className="die-text">
        {label}
      </text>
    </svg>
  );
}
