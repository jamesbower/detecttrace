// Importing this module registers the built-in pages, in navigation order.
// Each icon is an SVG path's `d`, drawn stroked in a 24 by 24 box.
import { registerPage } from "../registry";
import { DATA_PATH } from "../router";
import { Cases } from "./Cases";
import { Data } from "./Data";
import { Limits } from "./Limits";
import { Overview } from "./Overview";
import { SkippedSteps } from "./SkippedSteps";
import { VerdictMatrix } from "./VerdictMatrix";
import { Versions } from "./Versions";
import { WeeklyTrend } from "./WeeklyTrend";

registerPage({
  path: "/",
  title: "Overview",
  icon: "M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10",
  order: 0,
  component: Overview,
});
registerPage({
  path: "/versions",
  title: "Versions",
  icon: "M6 3v12M6 15a3 3 0 100 6 3 3 0 000-6zM18 9a3 3 0 100-6 3 3 0 000 6zM18 9c0 6-12 3-12 6",
  order: 1,
  component: Versions,
});
registerPage({
  path: "/skipped",
  title: "Skipped steps",
  icon: "M4 4h16v16H4zM4 10h16M4 15h16M10 4v16M15 4v16",
  order: 2,
  component: SkippedSteps,
});
registerPage({
  path: "/trends",
  title: "Weekly trend",
  icon: "M3 20h18M4 16l5-6 4 4 7-9",
  order: 3,
  component: WeeklyTrend,
});
registerPage({
  path: "/verdicts",
  title: "Verdict matrix",
  icon: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  order: 4,
  component: VerdictMatrix,
});
registerPage({
  path: "/cases",
  title: "Cases",
  icon: "M5 3h14v18H5zM9 8h6M9 12h6M9 16h4",
  order: 5,
  component: Cases,
});
registerPage({
  path: DATA_PATH,
  title: "Data",
  icon: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  order: 6,
  component: Data,
});
registerPage({
  path: "/limits",
  title: "Limits",
  icon: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 8v.5M12 11v5",
  order: 7,
  component: Limits,
});
