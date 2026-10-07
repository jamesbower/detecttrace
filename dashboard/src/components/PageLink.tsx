import { followLink, toHref } from "../router";

import type * as React from "react";

// No target or download: a click on either needs the browser's own handling, and followLink
// opens every plain click in place.
type PageLinkProps = Omit<
  React.AnchorHTMLAttributes<HTMLAnchorElement>,
  "href" | "onClick" | "target" | "download"
> & {
  path: string;
  query?: URLSearchParams;
  /** The id of a heading on the page to open at. */
  section?: string | null;
};

/** A link to a dashboard page, or a section of it, at its hash or its path, as the router
 * routes. */
export function PageLink({ path, query = new URLSearchParams(), section, ...anchorProps }: PageLinkProps) {
  return (
    <a
      {...anchorProps}
      href={toHref(path, query, section)}
      onClick={(event) => {
        followLink(event, path, query, section);
      }}
    />
  );
}
