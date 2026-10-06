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
};

/** A link to a dashboard page, at its hash or its path, as the router routes. */
export function PageLink({ path, query = new URLSearchParams(), ...anchorProps }: PageLinkProps) {
  return (
    <a
      {...anchorProps}
      href={toHref(path, query)}
      onClick={(event) => {
        followLink(event, path, query);
      }}
    />
  );
}
