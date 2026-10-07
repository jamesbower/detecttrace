// Fixed, data-independent explanations of the measure names. Keep each `short` text in step
// with its section in docs/metrics.md: tests/test_help_text.py checks the key phrases.
export type TermKey = "completeness" | "agreement" | "kappa" | "dangerous";
type Term = { readonly label: string; readonly short: string; readonly anchor: string };

export const TERMS: Readonly<Record<TermKey, Term>> = {
  completeness: {
    label: "Evidence completeness",
    short:
      "The share of this alert class's checklist items that the agent's successful tool calls satisfied, averaged over cases. 100% means every item was satisfied in every case.",
    anchor: "evidence-completeness",
  },
  agreement: {
    label: "Verdict agreement",
    short:
      "The share of cases where the agent's verdict equals the analyst's: agreed cases divided by cases with both verdicts.",
    anchor: "verdict-agreement",
  },
  kappa: {
    label: "Chance-corrected agreement (κ)",
    short:
      "Cohen's kappa: agreement corrected for the agreement expected by chance from each side's mix of verdicts. 1 means perfect agreement; 0 means agreement no better than chance.",
    anchor: "chance-corrected-agreement",
  },
  dangerous: {
    label: "Dangerous false closes",
    short:
      "Cases where the analyst said true positive and the agent said false positive or benign: the agent closed a real threat.",
    anchor: "dangerous-false-closes",
  },
};
