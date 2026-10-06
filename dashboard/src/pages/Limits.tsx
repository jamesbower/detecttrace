import { PageHead } from "../components/PageHead";
import "./Limits.css";

import type { PageProps } from "../registry";

export function Limits({ view }: PageProps) {
  return (
    <>
      <PageHead eyebrow="Limits" title="Limits" description="What this dashboard does not tell you." />
      <dl className="limits">
        {view.limits.map((limit) => (
          <div key={limit.term} className="limits-item">
            <dt className="limits-term">{limit.term}</dt>
            <dd className="limits-text">{limit.text}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}
