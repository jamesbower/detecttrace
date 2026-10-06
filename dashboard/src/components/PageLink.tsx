import { followLink, toHref } from "../router";

import type * as React from "react";

type PageLinkProps = Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href" | "onClick"> & {
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
