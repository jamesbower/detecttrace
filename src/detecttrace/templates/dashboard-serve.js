(function () {
  "use strict";
  // Served pages only: asks the server that sent the page whether newer results exist, and says
  // so. It never reloads by itself, so filters and scroll position stay. Text goes through
  // textContent; nothing is parsed as HTML.
  var POLL_MS = 30000;
  var NEW_DATA_TEXT = "New data is available.";
  var NO_TIME_TEXT = "an earlier update";

  function parseTime(text) {
    var ms = typeof text === "string" ? Date.parse(text) : NaN;
    return isNaN(ms) ? null : ms;
  }

  // A newer snapshot can share the generation: a case that settles later is added without
  // any new write, so the time it was computed breaks the tie.
  function hasNewerResults(status, page) {
    if (typeof status.generation !== "number") return false;
    if (status.generation !== page.generation) return status.generation > page.generation;
    var statusTime = parseTime(status.updated_at);
    var pageTime = parseTime(page.updatedAt);
    return statusTime !== null && pageTime !== null && statusTime > pageTime;
  }

  // Fixed format and zone, so every reader sees the same text whatever their locale.
  function formatTime(text) {
    var ms = parseTime(text);
    if (ms === null) return NO_TIME_TEXT;
    return new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";
  }

  function decideBar(status, page) {
    return {
      isNewData: hasNewerResults(status, page),
      errorText: typeof status.last_error === "string" && status.last_error !== ""
        ? "Showing data from " + formatTime(status.updated_at) + "; the last update failed."
        : null
    };
  }

  function barKey(bar) {
    return String(bar.isNewData) + "\n" + String(bar.errorText);
  }

  function renderBar(region, bar) {
    while (region.firstChild) region.removeChild(region.firstChild);
    if (!bar.isNewData && bar.errorText === null) return;
    var box = document.createElement("div");
    box.className = "banner";
    var text = document.createElement("p");
    var parts = [];
    if (bar.errorText !== null) parts.push(bar.errorText);
    if (bar.isNewData) parts.push(NEW_DATA_TEXT);
    text.textContent = parts.join(" ");
    box.appendChild(text);
    if (bar.isNewData) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "more";
      button.textContent = "Reload";
      button.addEventListener("click", function () { location.reload(); });
      box.appendChild(button);
    }
    region.appendChild(box);
  }

  function startPolling(region) {
    var page = {
      generation: Number(region.getAttribute("data-generation")),
      updatedAt: region.getAttribute("data-updated-at")
    };
    var shownKey = barKey({ isNewData: false, errorText: null });
    var hasLoggedFailure = false;
    function poll() {
      fetch("/api/status", { credentials: "same-origin", cache: "no-store" })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then(function (status) {
          hasLoggedFailure = false;
          var bar = decideBar(status, page);
          var key = barKey(bar);
          // Unchanged text is not rewritten, so screen readers announce only real changes.
          if (key !== shownKey) {
            shownKey = key;
            renderBar(region, bar);
          }
        })
        .catch(function (error) {
          // A server restart or a dropped network is routine; one console line per outage.
          if (!hasLoggedFailure) {
            hasLoggedFailure = true;
            console.warn("detecttrace: could not check for newer results: " + String(error));
          }
        })
        .then(function () { setTimeout(poll, POLL_MS); });
    }
    setTimeout(poll, POLL_MS);
  }

  if (typeof document !== "undefined") {
    var region = document.getElementById("dt-serve");
    if (region !== null) startPolling(region);
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      POLL_MS: POLL_MS,
      hasNewerResults: hasNewerResults,
      formatTime: formatTime,
      decideBar: decideBar,
      barKey: barKey
    };
  }
})();
