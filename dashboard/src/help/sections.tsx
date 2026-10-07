// The Help page's built-in sections, in reading order. Importing this module registers them.
// Each measure's section id and title are its term's anchor and label in terms.ts, so a term's
// "More in Help" link opens it. Keep the facts in step with docs/metrics.md, docs/ui.md and
// docs/checklists.md: tests/test_help_text.py checks that the text describes without judging.
import { PageLink } from "../components/PageLink";
import { registerHelpSection } from "../registry";
import { TERMS } from "./terms";

const DOCS_URL = "https://github.com/jamesbower/detecttrace/blob/main/docs";

registerHelpSection({ id: "how-it-works", title: "How DetectTrace works", order: 0, body: HowItWorks });
registerHelpSection({ id: "pages", title: "The pages", order: 1, body: Pages });
registerHelpSection({
  id: TERMS.completeness.anchor,
  title: TERMS.completeness.label,
  order: 2,
  body: EvidenceCompleteness,
});
registerHelpSection({ id: TERMS.agreement.anchor, title: TERMS.agreement.label, order: 3, body: VerdictAgreement });
registerHelpSection({ id: TERMS.kappa.anchor, title: TERMS.kappa.label, order: 4, body: ChanceCorrectedAgreement });
registerHelpSection({
  id: TERMS.dangerous.anchor,
  title: TERMS.dangerous.label,
  order: 5,
  body: DangerousFalseCloses,
});
registerHelpSection({ id: "reading-the-numbers", title: "Reading the numbers", order: 6, body: ReadingTheNumbers });
registerHelpSection({ id: "using-the-app", title: "Using the app", order: 7, body: UsingTheApp, modes: ["ui"] });
registerHelpSection({ id: "more", title: "More", order: 8, body: More });

function HowItWorks() {
  return (
    <>
      <p>
        DetectTrace reads the traces of an AI SOC agent and the verdicts your analysts gave the same alerts, and
        compares the two.
      </p>
      <p>
        A case is one agent run: an <code>invoke_agent</code> root span that carries a case ID. It is joined to the
        analyst&apos;s verdict by that case ID, so a case is scored when its case ID is in both the traces and the
        verdict file. Each case&apos;s alert class comes from the verdict file.
      </p>
      <p>
        A checklist lists the investigation steps your playbook expects for one alert class, as tool calls. Each
        case&apos;s successful tool calls are checked against it. Evidence completeness and skipped steps need a
        checklist; an alert class without one gets the verdict measures only.
      </p>
      <p>
        Each measure uses the cases it can. Verdict agreement, chance-corrected agreement, the verdict matrix and
        dangerous false closes use the cases with both an analyst verdict and an agent verdict. Evidence completeness
        and skipped steps use every case of a class that has a checklist. A verdict is missing when its label has no
        mapping, when the agent run has no verdict, or when a case&apos;s verdict rows disagree; the Data page lists
        each of these.
      </p>
    </>
  );
}

function Pages() {
  return (
    <dl className="help-pages">
      <dt>Overview</dt>
      <dd>
        The cases and period covered, the coverage lines, and each class&apos;s dangerous false closes. For the class
        you pick, its version table, with links to that class&apos;s skipped steps, weekly trend and verdict matrix.
      </dd>
      <dt>Versions</dt>
      <dd>
        For each alert class, one row per prompt version, in the order each version first appeared: evidence
        completeness, verdict agreement and chance-corrected agreement, each with n and a 95% interval, and dangerous
        false closes. Up to six versions are shown per class; the rest are pooled into one row.
      </dd>
      <dt>Skipped steps</dt>
      <dd>
        For each checklist step and each version, the share of cases where the step was not satisfied: not called,
        called with the wrong arguments, or failed. Each cell shows the rate and the counts.
      </dd>
      <dt>Weekly trend</dt>
      <dd>
        Evidence completeness and verdict agreement per ISO week, with one line per version and one for all versions
        together. A case&apos;s week comes from its agent run&apos;s start time, in UTC. A dashed rule marks the first
        week of each version, and a hollow point has fewer than 10 cases.
      </dd>
      <dt>Verdict matrix</dt>
      <dd>
        The analyst&apos;s verdict in rows against the agent&apos;s in columns, all versions together. The two cells
        where a dangerous false close falls are outlined, and flagged when they hold any case.
      </dd>
      <dt>Cases</dt>
      <dd>
        A table of the cases, with filters, such as one for dangerous false closes. Open a row to see its tool calls
        and the checklist steps it did not satisfy.
      </dd>
      <dt>Data</dt>
      <dd>
        The coverage lines, and the input problems found while reading traces and verdicts, grouped, with how to fix
        each. In <code>detecttrace ui</code>, it is also where you upload files and confirm the configuration.
      </dd>
      <dt>Limits</dt>
      <dd>What this dashboard does not tell you.</dd>
    </dl>
  );
}

function EvidenceCompleteness() {
  return (
    <>
      <p>
        For one case, the share of its alert class&apos;s checklist items that the agent&apos;s successful tool calls
        satisfied. An item is satisfied when at least one successful call to its tool passes every argument rule the
        item sets. For a class or a version, it is the mean over its cases. A class without a checklist has no
        evidence completeness.
      </p>
      <p className="help-formula">
        <code>completeness = satisfied items / checklist items</code>, averaged over cases
      </p>
      <h3>Example</h3>
      <p>
        A class&apos;s checklist has 4 items. In one case, the agent&apos;s successful calls satisfy 3 of them: 3 / 4 =
        75%. In a second case they satisfy all 4: 100%. Over these two cases, evidence completeness is (75% + 100%) / 2
        = 87.5%, shown as 88%.
      </p>
    </>
  );
}

function VerdictAgreement() {
  return (
    <>
      <p>
        The share of cases where the agent&apos;s verdict equals the analyst&apos;s, over the three verdicts true
        positive, false positive and benign. Only cases with both verdicts count.
      </p>
      <p className="help-formula">
        <code>agreement = agreed / n</code>
      </p>
      <p>Here agreed is the number of cases where the two verdicts are the same, and n is the cases with both.</p>
      <h3>Example</h3>
      <p>
        Of 50 cases with both verdicts, the agent&apos;s verdict equals the analyst&apos;s in 42: agreement is 42 / 50 =
        84%.
      </p>
    </>
  );
}

function ChanceCorrectedAgreement() {
  return (
    <>
      <p>
        Cohen&apos;s kappa. It corrects verdict agreement for the agreement expected by chance from each side&apos;s mix
        of verdicts. κ is 1 for perfect agreement and 0 for agreement no better than chance. It can be negative.
      </p>
      <p className="help-formula">
        <code>κ = (p_o − p_e) / (1 − p_e)</code>
      </p>
      <ul>
        <li>
          <code>p_o</code> is the observed agreement: the share of cases where the two verdicts are the same.
        </li>
        <li>
          <code>p_e</code> is the agreement expected by chance: the sum, over the three verdicts, of the analyst&apos;s
          share of that verdict times the agent&apos;s share of it.
        </li>
      </ul>
      <h3>Example</h3>
      <p>
        Of 100 cases, the analyst said true positive in 40 and benign in 60; the agent said true positive in 30 and
        benign in 70. They agree in 25 true positives and 55 benign cases, so <code>p_o</code> = 0.80. By chance,{" "}
        <code>p_e</code> = 0.40 × 0.30 + 0.60 × 0.70 = 0.54. So κ = (0.80 − 0.54) / (1 − 0.54) = 0.57.
      </p>
      <p>
        An agent that says benign for every case of a class that is 85% benign agrees 85% of the time, and has κ = 0.
      </p>
    </>
  );
}

function DangerousFalseCloses() {
  return (
    <>
      <p>
        Cases where the analyst said true positive and the agent said false positive or benign: the agent closed a
        real threat. Each version shows its count, and the Cases page has a filter that lists them.
      </p>
      <p className="help-formula">
        <code>analyst true_positive, agent false_positive or benign</code>
      </p>
      <p>
        True positives with no agent verdict are counted apart. They are cases where the analyst said true positive
        and the agent&apos;s verdict is missing or unmapped. As the agent&apos;s answer is unknown, they are neither
        dangerous false closes nor part of verdict agreement. A version row lists up to three of their case IDs.
      </p>
      <h3>Example</h3>
      <p>
        Of 40 cases the analyst marked true positive, the agent said true positive in 33, false positive in 2 and benign
        in 4, and gave no verdict in 1. That is 6 dangerous false closes, and 1 true positive with no agent verdict.
      </p>
    </>
  );
}

function ReadingTheNumbers() {
  return (
    <>
      <p>
        Every value shows its n: the number of cases that value is based on. For verdict agreement and κ, that is the
        cases with both verdicts; for evidence completeness and skipped steps, the cases. So two values in one row can
        have different n. A value based on fewer than 10 cases is marked &ldquo;Few cases.&rdquo;
      </p>
      <p>Every interval is a 95% interval:</p>
      <ul>
        <li>Verdict agreement: the Wilson score interval.</li>
        <li>
          κ: an analytic interval, κ ± 1.96 × its standard error, at 100 or more cases; a percentile bootstrap of 1,000
          resamples of the cases below that.
        </li>
        <li>
          Evidence completeness: a percentile bootstrap of the mean from 2 to 29 cases, and Student&apos;s t-interval
          for the mean at 30 or more.
        </li>
      </ul>
      <p>Skipped-step rates and weekly trend points have no interval. An interval is also left out when:</p>
      <ul>
        <li>an evidence completeness value has one case, or all its cases have the same value;</li>
        <li>
          fewer than 900 of the 1,000 resamples give a κ, as κ is undefined in a resample where both sides give one and
          the same verdict for every case;
        </li>
        <li>
          one side gives the same verdict for every case: κ is then exactly 0, and an interval of zero width would look
          precise.
        </li>
      </ul>
      <p>
        Percentages are whole numbers. A value just under 100% shows as &ldquo;&gt;99%&rdquo; and one just above 0 as
        &ldquo;&lt;1%&rdquo;, so &ldquo;100%&rdquo; and &ldquo;0%&rdquo; mean exactly that.
      </p>
    </>
  );
}

function UsingTheApp() {
  return (
    <>
      <p>
        On the Data page, upload your trace files, verdict files and checklist files, each on its own card: choose
        files with the card&apos;s button, or drop them on it. Each card shows what was stored from each file and any
        problems found in it.
      </p>
      <p>
        Once traces and verdicts are both stored, DetectTrace proposes a configuration: which span attribute holds
        each field, such as the case ID and the agent verdict. Correct what is wrong, map each verdict label it
        can&apos;t map by itself to true positive, false positive or benign, and choose Confirm.
      </p>
      <p>
        To change the configuration later, choose Change configuration, edit it and confirm again. Nothing needs to be
        uploaded again.
      </p>
      <p>
        Clear all data, below the upload cards, asks you to confirm, then deletes the uploaded data, the configuration
        and the checklists. It can&apos;t be undone.
      </p>
      <p>
        After you confirm, the app computes the dashboard in the background, and the page reloads itself when new
        results are ready. While you have unsaved changes, a file uploading or a card&apos;s results not yet cleared,
        it offers a Reload button instead. New uploads after that update the dashboard by themselves.
      </p>
    </>
  );
}

function More() {
  return (
    <>
      <ul>
        <li>
          <a href={`${DOCS_URL}/metrics.md`} rel="noreferrer">
            Metrics
          </a>
          : every number, its interval and its rounding.
        </li>
        <li>
          <a href={`${DOCS_URL}/attributes.md`} rel="noreferrer">
            Trace attributes
          </a>
          : where each field of a case comes from in the traces.
        </li>
        <li>
          <a href={`${DOCS_URL}/checklists.md`} rel="noreferrer">
            Checklists
          </a>
          : the checklist format and its argument rules.
        </li>
        <li>
          <a href={`${DOCS_URL}/ui.md`} rel="noreferrer">
            Running locally in your browser
          </a>
          : the <code>detecttrace ui</code> app.
        </li>
        <li>
          <a href={`${DOCS_URL}/serve.md`} rel="noreferrer">
            Running as a service
          </a>
          : the <code>detecttrace serve</code> service.
        </li>
      </ul>
      <p>
        What this dashboard does not tell you is on the <PageLink path="/limits">Limits</PageLink> page.
      </p>
    </>
  );
}
