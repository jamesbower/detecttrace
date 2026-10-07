import { useId } from "react";

import { PageHead } from "../components/PageHead";
import { PageLink } from "../components/PageLink";
import { listHelpSections } from "../registry";
import { HELP_PATH } from "../router";
import "./Help.css";

import type { View } from "../view";

// Needs only the view, so a waiting page, which has no results, can show it.
type HelpProps = { view: View };

/** What each page and measure means. The app moves focus to a section the address names. */
export function Help({ view }: HelpProps) {
  const sections = listHelpSections(view.mode);
  // Generated, so no section's id can match it.
  const contentsLabelId = useId();
  return (
    <>
      <PageHead
        eyebrow="Help"
        title="How DetectTrace works"
        description="What each page shows, how each measure is computed, and how to read its numbers."
      />
      <nav className="help-contents" aria-labelledby={contentsLabelId}>
        <p id={contentsLabelId} className="help-contents-label">
          On this page
        </p>
        <ul>
          {sections.map((section) => (
            <li key={section.id}>
              <PageLink path={HELP_PATH} section={section.id}>
                {section.title}
              </PageLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="help">
        {sections.map(({ id, title, body: Body }) => (
          <section key={id} className="help-section" aria-labelledby={id}>
            <h2 id={id} className="help-title" tabIndex={-1}>
              {title}
            </h2>
            <Body />
          </section>
        ))}
      </div>
    </>
  );
}
