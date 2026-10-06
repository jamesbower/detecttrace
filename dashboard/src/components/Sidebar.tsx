import "./Sidebar.css";

import type { PageDef } from "../registry";

type SidebarProps = { pages: readonly PageDef[]; currentPath: string };

export function Sidebar({ pages, currentPath }: SidebarProps) {
  return (
    <aside className="sidebar">
      <p className="sidebar-brand">
        <span className="sidebar-brand-mark" aria-hidden="true">
          DT
        </span>
        <span>
          Detect<span className="sidebar-brand-accent">Trace</span>
        </span>
      </p>
      <nav aria-label="Pages">
        <ul className="sidebar-links">
          {pages.map((page) => (
            <li key={page.path}>
              <a
                className="sidebar-link"
                href={`#${page.path}`}
                aria-current={page.path === currentPath ? "page" : undefined}
              >
                {page.icon !== "" && (
                  <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                    <path d={page.icon} />
                  </svg>
                )}
                <span className="sidebar-label">{page.title}</span>
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
