import { useId } from "react";

import { ClassPanel, ClassSelector } from "../components/ClassSelector";
import { Kpi } from "../components/Kpi";
import { PageHead } from "../components/PageHead";
import { PanelSlot } from "../components/PanelSlot";
import { VersionTable } from "../components/VersionTable";
import { toHash } from "../router";
import { useSelectedClass } from "../use-selected-class";
import "./Overview.css";

import type { PageProps } from "../registry";
import type { ClassView, CoverageView } from "../view";

export function Overview({ view, results }: PageProps) {
  const panelId = useId();
  const selected = useSelectedClass(view.classes);
  const classQuery = new URLSearchParams(selected === undefined ? {} : { class: selected.anchor });

  return (
    <>
      <PageHead
        eyebrow="Overview"
        title="Agent vs. analyst"
        description="How your SOC agent compares with your analysts, per alert class and per prompt version."
      />
      <dl className="overview-kpis">
        <Kpi label="Cases" value={view.header.cases_text} context={`Versions found: ${view.header.versions_text}`} />
        <Kpi label="Period" value={view.header.period_text} />
        <Kpi
          label="Coverage"
          value={
            <ul className="overview-lines">
              {view.coverage.map((coverage) => (
                <li key={coverage.text}>{coverage.text}</li>
              ))}
            </ul>
          }
          context={toCoverageHints(view.coverage)}
        />
        <Kpi
          label="Dangerous false closes"
          value={
            <ul className="overview-lines">
              {view.classes.map((alertClass) => (
                <li key={alertClass.anchor}>
                  <span className="overview-class-name">{alertClass.name}</span>: {alertClass.confusion.dangerous_text}
                </li>
              ))}
            </ul>
          }
          context="The analyst said true positive; the agent said false positive or benign."
        />
      </dl>
      <PanelSlot name="overview-after-kpis" view={view} results={results} />
      {view.header.low_coverage_text !== null && (
        <p className="overview-alert">
          <a href={toHash("/data-notes", classQuery)}>
            <WarnIcon />
            <span>{view.header.low_coverage_text}</span>
          </a>
        </p>
      )}
      <ul className="overview-sources" aria-label="Data sources">
        {view.header.sources.map((source) => (
          <li key={source.name} className="overview-chip">
            {source.name}: <code>{source.path}</code>
          </li>
        ))}
      </ul>
      <ClassSelector classes={view.classes} panelId={panelId} />
      {selected !== undefined && (
        <ClassPanel classes={view.classes} selected={selected} panelId={panelId} className="overview-panel">
          <VersionTable alertClass={selected} view={view} results={results} />
          <nav className="overview-tiles" aria-label={`More for ${selected.name}`}>
            {listTiles(selected).map((tile) => (
              <a key={tile.path} className="overview-tile" href={toHash(tile.path, classQuery)}>
                <span className="overview-tile-title">{tile.title}</span>
                <span className="overview-tile-figure">{tile.figure}</span>
                <span className="overview-tile-context">{tile.context}</span>
              </a>
            ))}
          </nav>
        </ClassPanel>
      )}
    </>
  );
}

function toCoverageHints(coverage: readonly CoverageView[]): string | undefined {
  const hints = coverage.flatMap((item) => (item.hint === null ? [] : [item.hint]));
  return hints.length === 0 ? undefined : hints.join(" ");
}

function listTiles(alertClass: ClassView) {
  return [
    {
      path: "/skipped",
      title: "Skipped steps",
      figure: alertClass.skipped.empty_text ?? alertClass.skipped.steps_text,
      context: "Which checklist steps the agent missed",
    },
    {
      path: "/trends",
      title: "Weekly trend",
      figure: alertClass.trend.period_text,
      context: "Per-version lines and rollbacks",
    },
    {
      path: "/verdicts",
      title: "Verdict matrix",
      figure: alertClass.confusion.dangerous_text,
      context: alertClass.confusion.n_text,
    },
  ];
}

function WarnIcon() {
  return (
    <svg className="overview-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M8 1.8 15 14H1z" />
      <path d="M8 6.2v3.6M8 11.8v.2" />
    </svg>
  );
}
