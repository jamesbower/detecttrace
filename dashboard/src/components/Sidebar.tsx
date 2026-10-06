import { PageLink } from "./PageLink";
import "./Sidebar.css";

import type { PageDef } from "../registry";

type SidebarProps = {
  /** The product name the view gives, shown as the brand. */
  title: string;
  pages: readonly PageDef[];
  currentPath: string;
  /** The selected class, carried to every page; page-specific filters are not. */
  classAnchor?: string | null;
};

export function Sidebar({ title, pages, currentPath, classAnchor = null }: SidebarProps) {
  const query = new URLSearchParams(classAnchor === null ? {} : { class: classAnchor });
  return (
    // A header, not an aside: the brand and the navigation are the site's banner.
    <header className="sidebar">
      <p className="sidebar-brand">
        <span className="sidebar-brand-mark" aria-hidden="true">
          DT
        </span>
        <span>{title}</span>
      </p>
      <nav aria-label="Pages">
        <ul className="sidebar-links">
          {pages.map((page) => (
            <li key={page.path}>
              <PageLink
                className="sidebar-link"
                path={page.path}
                query={query}
                aria-current={page.path === currentPath ? "page" : undefined}
              >
                {page.icon !== "" && (
                  <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                    <path d={page.icon} />
                  </svg>
                )}
                <span className="sidebar-label">{page.title}</span>
              </PageLink>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  );
}
