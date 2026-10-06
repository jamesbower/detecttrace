import { listPages } from "./registry";
import "./App.css";

export function App() {
  const pages = listPages();
  return (
    <main className="app">
      <h1 className="app-title">
        Detect<span className="app-title-accent">Trace</span>
      </h1>
      <nav aria-label="Pages">
        <ul className="app-pages">
          {pages.map((page) => (
            <li key={page.path}>{page.title}</li>
          ))}
        </ul>
      </nav>
    </main>
  );
}
