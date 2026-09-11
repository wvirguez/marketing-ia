import type { CSSProperties } from "react";

const paths = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  campaign: "m3 11 18-7v16L3 14z M7 15l2 6h4l-2-5 M3 11v3",
  content: "M6 3h9l4 4v14H6z M14 3v5h5 M9 12h7 M9 16h5",
  image: "M3 3h18v18H3z m0 14 5-5 5 5 3-3 5 5 M15 7h.01",
  calendar: "M4 5h16v16H4z M8 3v4 M16 3v4 M4 10h16 M8 14h2 M14 14h2",
  chart: "M4 3v18h17 M8 16v-5 M13 16V7 M18 16V4",
  settings: "M4 7h16 M4 17h16 M8 4v6 M16 14v6",
  spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M10 21h4",
  arrow: "M5 12h14 m-5-5 5 5-5 5",
  plus: "M12 5v14 M5 12h14",
  check: "m5 12 4 4L19 6",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  chevron: "m9 5 7 7-7 7",
  target: "M21 12a9 9 0 1 1-9-9 M17 12a5 5 0 1 1-5-5 M12 12l9-9 M16 3h5v5",
  logout: "M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4 M10 17l5-5-5-5 M15 12H3",
} as const;
export type IconName = keyof typeof paths;
export function Icon({ name, size = 20, style }: { name: IconName; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}><path d={paths[name]} /></svg>;
}
