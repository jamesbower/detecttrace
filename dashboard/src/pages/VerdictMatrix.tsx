// Every class at once, as on the single-file page: the matrices are small, and side by side
// they compare classes without a selector.
import { Matrix } from "../components/Matrix";
import { PageHead } from "../components/PageHead";
import "./analysis-panel.css";

import type { PageProps } from "../registry";

export function VerdictMatrix({ view }: PageProps) {
  return (
    <>
      <PageHead
        eyebrow="Verdict matrix"
        title="Verdict matrix"
        description="Agent verdict against analyst verdict, all versions together. Outlined cells are where a dangerous false close would fall: the analyst found a true positive, and the agent closed it as a false positive or benign. Cells with any such case are also flagged."
      />
      <div className="analysis-grid">
        {view.classes.map((alertClass) => (
          <section
            key={alertClass.anchor}
            className="analysis-panel"
            aria-labelledby={`verdicts-${alertClass.anchor}`}
          >
            <h2 id={`verdicts-${alertClass.anchor}`} className="analysis-panel-title">
              <span>{alertClass.name}</span>{" "}
              <span className="analysis-panel-sub">
                {`All versions · ${alertClass.confusion.n_text} · ${alertClass.confusion.dangerous_text}`}
              </span>
            </h2>
            <Matrix confusion={alertClass.confusion} />
          </section>
        ))}
      </div>
    </>
  );
}
