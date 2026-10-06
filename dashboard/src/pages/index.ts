// Importing this module registers the built-in pages.
import { registerPage } from "../registry";
import { Overview } from "./Overview";

registerPage({ path: "/", title: "Overview", icon: "home", order: 0, component: Overview });
