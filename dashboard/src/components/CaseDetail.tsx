// An opened case: its tool calls with their arguments (never their results) and the
// checklist steps it did not satisfy.
import { toDetailModel } from "../case-rows";
import "./CaseDetail.css";

import type { CaseRow } from "../case-rows";
import type { CaseDetail as CaseDetailData } from "../results";

type CaseDetailProps = { row: CaseRow; detail: CaseDetailData | null };

export function CaseDetail({ row, detail }: CaseDetailProps) {
  const model = toDetailModel(row, detail);
  return (
    <div className="case-detail">
      <div>
        <h2 className="case-detail-heading">Tool calls, in order</h2>
        {model.calls === null || model.callsNote !== null ? (
          <p className={model.calls === null ? "case-detail-missing" : undefined}>{model.callsNote}</p>
        ) : (
          <ol className="case-calls">
            {model.calls.map((call, index) => (
              // Calls are shown in their recorded order and never reorder, so the index is stable.
              <li key={index}>
                <span className="case-call-status" data-failed={call.isFailed}>
                  {call.statusLabel}
                </span>
                <span className="case-call-main">
                  <code className="case-call-tool">{call.tool}</code>
                  <code className="case-call-args">{call.args}</code>
                </span>
                <span className="case-call-duration">{call.duration}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
      <div>
        <h2 className="case-detail-heading">Checklist steps not satisfied</h2>
        {model.stepsNote !== null ? (
          <p>{model.stepsNote}</p>
        ) : (
          <ul className="case-steps">
            {model.steps.map((step, index) => (
              <li key={index}>
                <span className="case-step-kind" data-status={step.status}>
                  {step.label}
                </span>
                <code>{step.item}</code>
                <span className="case-step-why">{step.why}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
