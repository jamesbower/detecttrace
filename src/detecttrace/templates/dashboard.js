(function () {
  "use strict";
  // Every string from the results goes through textContent; nothing is parsed as HTML.
  var PAGE_SIZE = 100;
  var COLUMN_COUNT = 8;
  var DETAIL_MISSING = "Details not included. Raise dashboard.max_detail_cases.";
  var MAX_REASON_CHARS = 120;
  var VERDICT_LABELS = new Map([
    ["true_positive", "True positive"],
    ["false_positive", "False positive"],
    ["benign", "Benign"]
  ]);

  // Same rule as to_visible_text in summary.py, so a bidi override or line break cannot make
  // one case ID read as another. The u flag matches code points: surrogate pairs stay whole.
  function toVisibleText(text) {
    return text.replace(/[\p{C}\p{Zl}\p{Zp}]/gu, function (char) {
      var code = char.codePointAt(0);
      var hex = code.toString(16);
      if (code < 0x100) return "\\x" + hex.padStart(2, "0");
      if (code < 0x10000) return "\\u" + hex.padStart(4, "0");
      return "\\U" + hex.padStart(8, "0");
    });
  }

  function decodeRows(caseRows) {
    var strings = caseRows.strings;
    var columns = caseRows.columns;
    var checklists = new Map();
    caseRows.checklists.forEach(function (checklist) {
      checklists.set(checklist.class_index, checklist.items.map(function (item) { return strings[item]; }));
    });
    function toVerdict(code) {
      return code === caseRows.unknown_verdict_code ? null : caseRows.verdict_codes[code];
    }
    var rows = [];
    for (var i = 0; i < columns.case_id.length; i++) {
      var version = columns.version[i];
      rows.push({
        index: i,
        caseId: columns.case_id[i],
        classIndex: columns.class_index[i],
        alertClass: strings[columns.class_index[i]],
        week: strings[columns.week[i]],
        version: version === null ? null : strings[version],
        analyst: toVerdict(columns.analyst[i]),
        agent: toVerdict(columns.agent[i]),
        satisfied: columns.satisfied[i],
        checklist: checklists.has(columns.class_index[i]) ? checklists.get(columns.class_index[i]) : null,
        notCalled: columns.not_called_items[i],
        wrongArguments: columns.wrong_argument_items[i],
        failed: columns.failed_items[i]
      });
    }
    return rows;
  }

  // Newest week first, then case ID; the column order is already by case ID, so the index
  // breaks ties and keeps the order stable in every engine.
  function orderRows(rows) {
    return rows.slice().sort(function (a, b) {
      if (a.week !== b.week) return a.week < b.week ? 1 : -1;
      return a.index - b.index;
    });
  }

  // Cases with an unknown verdict on either side are not compared, as in the metrics.
  function isDisagreement(row) {
    return row.analyst !== null && row.agent !== null && row.analyst !== row.agent;
  }

  function isDangerous(row) {
    return row.analyst === "true_positive" && (row.agent === "false_positive" || row.agent === "benign");
  }

  function filterRows(rows, key) {
    if (key === "disagreements") return rows.filter(isDisagreement);
    if (key === "dangerous") return rows.filter(isDangerous);
    if (key.indexOf("class:") === 0) {
      var classIndex = Number(key.slice(6));
      return rows.filter(function (row) { return row.classIndex === classIndex; });
    }
    return rows.slice();
  }

  function nextPage(matching, shown) {
    return matching.slice(shown, shown + PAGE_SIZE);
  }

  function formatCount(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function countLine(shown, total) {
    var noun = total === 1 ? "case" : "cases";
    return "Showing " + formatCount(shown) + " of " + formatCount(total) + " matching " + noun + ".";
  }

  function versionLabel(version) {
    return version === null ? "(no version)" : toVisibleText(version);
  }

  function verdictLabel(verdict) {
    return verdict === null ? "(no verdict)" : VERDICT_LABELS.get(verdict) || toVisibleText(verdict);
  }

  // The text cells, in column order.
  function rowTexts(row) {
    return [toVisibleText(row.caseId), toVisibleText(row.alertClass), toVisibleText(row.week),
      versionLabel(row.version), verdictLabel(row.analyst), verdictLabel(row.agent)];
  }

  function checklistLabel(row) {
    return row.satisfied === null || row.checklist === null ? "—" : row.satisfied + " of " + row.checklist.length;
  }

  // Rounded before the unit is chosen, so 999.7 ms reads "1.0 s", not "1000 ms".
  function formatDuration(ms) {
    var rounded = Math.round(ms);
    return rounded < 1000 ? rounded + " ms" : (ms / 1000).toFixed(1) + " s";
  }

  // Map, not a plain object: a case ID such as "__proto__" must not reach the prototype.
  function indexDetails(caseDetail) {
    var details = new Map();
    caseDetail.forEach(function (detail) { details.set(detail.case_id, detail); });
    return details;
  }

  function findDetail(details, caseId) {
    return details.has(caseId) ? details.get(caseId) : null;
  }

  // The unsatisfied steps, in checklist order. Only detail cases carry the failing rule.
  function toOutcomes(row, detail) {
    if (detail) {
      return detail.outcomes
        .filter(function (outcome) { return outcome.status !== "satisfied"; })
        .map(function (outcome) {
          return { item: toVisibleText(outcome.item), status: outcome.status, why: describeOutcome(outcome.status, outcome.reason, outcome.failed_rule) };
        });
    }
    if (row.checklist === null) return [];
    var found = [];
    row.notCalled.forEach(function (pos) { found.push([pos, "missed", "not_called"]); });
    row.wrongArguments.forEach(function (pos) { found.push([pos, "missed", "wrong_arguments"]); });
    row.failed.forEach(function (pos) { found.push([pos, "failed", null]); });
    found.sort(function (a, b) { return a[0] - b[0]; });
    return found.map(function (entry) {
      return { item: toVisibleText(row.checklist[entry[0]]), status: entry[1], why: describeOutcome(entry[1], entry[2], null) };
    });
  }

  function describeOutcome(status, reason, failedRule) {
    if (status === "failed") return "every call returned an error";
    if (reason === "not_called") return "not called";
    return failedRule ? "wrong arguments (" + toVisibleText(failedRule) + ")" : "wrong arguments";
  }

  // What an opened row shows. A note replaces a list that is missing or empty.
  function toDetailModel(row, detail) {
    var steps = row.checklist === null ? [] : toOutcomes(row, detail).map(function (outcome) {
      return { item: outcome.item, status: outcome.status, why: outcome.why, label: outcome.status === "failed" ? "Failed" : "Missed" };
    });
    var callsNote = null;
    if (!detail) callsNote = DETAIL_MISSING;
    else if (detail.calls.length === 0) callsNote = "No tool calls.";
    var stepsNote = null;
    if (row.checklist === null) stepsNote = "This alert class has no checklist.";
    else if (steps.length === 0) stepsNote = "None. Every checklist step was satisfied.";
    return { calls: detail ? detail.calls.map(toCallModel) : null, callsNote: callsNote, steps: steps, stepsNote: stepsNote };
  }

  function toCallModel(call) {
    var isFailed = call.status !== "success";
    return {
      isFailed: isFailed,
      statusLabel: isFailed ? "✕ Failed" : "✓ Success",
      tool: toVisibleText(call.tool),
      args: call.arguments === null ? "(no arguments)" : toVisibleText(call.arguments),
      duration: formatDuration(call.duration_ms)
    };
  }

  // Code points, not UTF-16 units, so shortening never splits a surrogate pair.
  function startupMessage(error) {
    var hasMessage = error !== null && typeof error === "object" && typeof error.message === "string";
    var chars = Array.from(toVisibleText(String(hasMessage ? error.message : error)));
    var reason = chars.length > MAX_REASON_CHARS ? chars.slice(0, MAX_REASON_CHARS - 1).join("") + "…" : chars.join("");
    return "The case table could not be built: " + reason + ".";
  }

  function startTable(results, reportError) {
    var rows = orderRows(decodeRows(results.case_rows));
    var details = indexDetails(results.case_detail);
    var tbody = document.getElementById("case-rows");
    var countNode = document.getElementById("case-count");
    var moreButton = document.getElementById("case-more");
    var filterButtons = Array.prototype.slice.call(document.querySelectorAll("#case-filters button"));
    var matching = [];
    var shown = 0;

    // A failure in a click handler would otherwise leave a half-drawn table and no message.
    function guarded(handler) {
      return function () {
        try {
          handler();
        } catch (error) {
          reportError(error);
        }
      };
    }

    function el(tag, className, text) {
      var node = document.createElement(tag);
      if (className) node.setAttribute("class", className);
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function applyFilter(key) {
      matching = filterRows(rows, key);
      shown = 0;
      tbody.replaceChildren();
      if (matching.length === 0) {
        var empty = el("tr");
        var cell = el("td", "", "No cases match this filter.");
        cell.setAttribute("colspan", String(COLUMN_COUNT));
        empty.appendChild(cell);
        tbody.appendChild(empty);
      }
      showMore(false);
    }

    function showMore(shouldMoveFocus) {
      var page = nextPage(matching, shown);
      var toggles = page.map(appendRow);
      shown += page.length;
      countNode.textContent = countLine(shown, matching.length);
      moreButton.hidden = shown >= matching.length;
      if (shouldMoveFocus && toggles.length > 0) toggles[0].focus();
    }

    function appendRow(row) {
      var tr = el("tr", "case-row");
      var texts = rowTexts(row);
      var idCell = el("td");
      var toggle = el("button", "row-toggle", texts[0]);
      toggle.setAttribute("type", "button");
      toggle.setAttribute("aria-expanded", "false");
      toggle.addEventListener("click", guarded(function () { toggleDetail(tr, toggle, row); }));
      idCell.appendChild(toggle);
      tr.appendChild(idCell);
      tr.appendChild(el("td", "mono", texts[1]));
      tr.appendChild(el("td", "mono", texts[2]));
      tr.appendChild(el("td", "", texts[3]));
      tr.appendChild(el("td", "", texts[4]));
      tr.appendChild(el("td", "", texts[5]));
      var resultCell = el("td");
      resultCell.appendChild(resultPill(row));
      tr.appendChild(resultCell);
      tr.appendChild(el("td", "num", checklistLabel(row)));
      tbody.appendChild(tr);
      return toggle;
    }

    function resultPill(row) {
      if (isDangerous(row)) return el("span", "pill bad", "⚠ Dangerous false close");
      if (isDisagreement(row)) return el("span", "pill disagree", "≠ Disagree");
      if (row.analyst === null || row.agent === null) return el("span", "pill unknown", "? Not compared");
      return el("span", "pill agree", "= Agree");
    }

    function toggleDetail(tr, toggle, row) {
      var isOpen = toggle.getAttribute("aria-expanded") === "true";
      var detailRow = document.getElementById("case-detail-" + row.index);
      if (!detailRow) {
        detailRow = buildDetailRow(row);
        tr.after(detailRow);
      }
      // Set only now: aria-controls must name an element that exists.
      toggle.setAttribute("aria-controls", "case-detail-" + row.index);
      detailRow.hidden = isOpen;
      toggle.setAttribute("aria-expanded", String(!isOpen));
    }

    function buildDetailRow(row) {
      var tr = el("tr", "detail-row");
      tr.setAttribute("id", "case-detail-" + row.index);
      var td = el("td");
      td.setAttribute("colspan", String(COLUMN_COUNT));
      var body = el("div", "detail-body");
      var model = toDetailModel(row, findDetail(details, row.caseId));
      body.appendChild(buildCalls(model));
      body.appendChild(buildSteps(model));
      td.appendChild(body);
      tr.appendChild(td);
      return tr;
    }

    function buildCalls(model) {
      var block = el("div");
      block.appendChild(el("h3", "", "Tool calls, in order"));
      if (model.calls === null) {
        block.appendChild(el("p", "no-detail", model.callsNote));
        return block;
      }
      if (model.callsNote !== null) {
        block.appendChild(el("p", "", model.callsNote));
        return block;
      }
      var list = el("ol", "calls");
      model.calls.forEach(function (call) {
        var li = el("li");
        li.appendChild(el("span", "status " + (call.isFailed ? "is-failed" : "is-ok"), call.statusLabel));
        var main = el("span", "call-main");
        main.appendChild(el("span", "call-tool", call.tool));
        main.appendChild(el("span", "call-args", call.args));
        li.appendChild(main);
        li.appendChild(el("span", "call-dur", call.duration));
        list.appendChild(li);
      });
      block.appendChild(list);
      return block;
    }

    function buildSteps(model) {
      var block = el("div");
      block.appendChild(el("h3", "", "Checklist steps not satisfied"));
      if (model.stepsNote !== null) {
        block.appendChild(el("p", "", model.stepsNote));
        return block;
      }
      var list = el("ul", "steps");
      model.steps.forEach(function (step) {
        var li = el("li");
        li.appendChild(el("span", "step-kind " + (step.status === "failed" ? "is-failed" : "is-missed"), step.label));
        li.appendChild(el("code", "", step.item));
        li.appendChild(el("span", "step-why", step.why));
        list.appendChild(li);
      });
      block.appendChild(list);
      return block;
    }

    filterButtons.forEach(function (button) {
      button.addEventListener("click", guarded(function () {
        filterButtons.forEach(function (other) { other.setAttribute("aria-pressed", String(other === button)); });
        applyFilter(button.getAttribute("data-filter"));
      }));
    });
    moreButton.addEventListener("click", guarded(function () { showMore(true); }));
    // Filled while hidden, so the live count line announces only later filter changes.
    applyFilter("all");
    document.getElementById("case-ui").hidden = false;
  }

  if (typeof document !== "undefined") {
    var status = document.getElementById("case-status");
    var reportError = function (error) {
      status.textContent = startupMessage(error);
      status.hidden = false;
    };
    try {
      startTable(JSON.parse(document.getElementById("dt-results").textContent), reportError);
      status.textContent = "";
      status.hidden = true;
    } catch (error) {
      reportError(error);
    }
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      PAGE_SIZE: PAGE_SIZE,
      DETAIL_MISSING: DETAIL_MISSING,
      toVisibleText: toVisibleText,
      rowTexts: rowTexts,
      decodeRows: decodeRows,
      orderRows: orderRows,
      isDisagreement: isDisagreement,
      isDangerous: isDangerous,
      filterRows: filterRows,
      nextPage: nextPage,
      formatCount: formatCount,
      countLine: countLine,
      versionLabel: versionLabel,
      verdictLabel: verdictLabel,
      checklistLabel: checklistLabel,
      formatDuration: formatDuration,
      indexDetails: indexDetails,
      findDetail: findDetail,
      toOutcomes: toOutcomes,
      toDetailModel: toDetailModel,
      startupMessage: startupMessage
    };
  }
})();
