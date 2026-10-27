// CiteCheck front end: tabs, the three forms, and report progress polling. No build step.
(function () {
  "use strict";

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  async function postJSON(url, body) {
    var resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    var data = {};
    try { data = await resp.json(); } catch (e) { /* non-JSON error page */ }
    if (!resp.ok) {
      var detail = data.detail;
      if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg; }).join("; ");
      throw new Error(detail || "Request failed (" + resp.status + ")");
    }
    return data;
  }

  function busy(form, on, label) {
    var btn = form.querySelector('button[type="submit"]');
    if (!btn) return;
    if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || "Working…"; btn.disabled = true; }
    else { btn.textContent = btn.dataset.label || btn.textContent; btn.disabled = false; }
  }

  function showError(el, err) {
    el.innerHTML = '<p class="error-msg" role="alert">' + esc(err.message || err) + "</p>";
  }

  // Shared rendering for one answer (quick check + paste).
  function renderAnswer(el, data) {
    if (data.error) { showError(el, data.error); return; }
    var verdict = data.cited
      ? '<p class="verdict yes">Yes: ' + esc(data.domain) + " is cited (source #" + data.positions.join(", #") + ")</p>"
      : '<p class="verdict no">No: ' + esc(data.domain) + " is not among the " + data.citations.length + " cited sources</p>";
    var sources = data.citations.map(function (c) {
      var own = data.positions.indexOf(c.position) !== -1;
      return '<li class="' + (own ? "own" : "") + '"><a href="' + esc(c.url) + '" target="_blank" rel="nofollow noopener">' +
        esc(c.title || c.domain) + '</a> <span class="muted">' + esc(c.domain) + "</span></li>";
    }).join("");
    var search = "";
    if (data.retrieved && data.retrieved.length) {
      search = data.retrieved_rank
        ? '<p class="hint">Your site was #' + data.retrieved_rank + " in the " + data.retrieved.length +
          " search results the AI read" + (data.cited ? "." : ", but it cited others.") + "</p>"
        : '<p class="hint">Your site wasn\'t in the ' + data.retrieved.length +
          " search results the AI read, so it couldn't be cited.</p>";
    }
    el.innerHTML = verdict + search +
      (data.simulated ? '<p class="hint">Simulated answer (no API key configured).</p>' : "") +
      "<h3>Cited sources</h3>" + (sources ? '<ol class="sources">' + sources + "</ol>" : '<p class="hint">No sources were cited.</p>') +
      (data.answer ? '<details class="answer"><summary>Show the answer</summary><p class="answer-text">' + esc(data.answer) + "</p></details>" : "") +
      (data.cited ? "" : '<p class="hint">Want to know why? Run a <a href="#" data-goto="report">full report</a>.</p>');
  }

  function selectTab(name) {
    document.querySelectorAll("[data-tab]").forEach(function (tab) {
      tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
    });
    document.querySelectorAll("[data-panel]").forEach(function (panel) {
      panel.hidden = panel.dataset.panel !== name;
    });
  }

  document.addEventListener("click", function (ev) {
    var tab = ev.target.closest("[data-tab]");
    if (tab) { selectTab(tab.dataset.tab); return; }
    var go = ev.target.closest("[data-goto]");
    if (go) {
      ev.preventDefault();
      var site = document.querySelector('#quick-form [name="site"]');
      var target = document.querySelector('#report-form [name="site"]');
      if (site && target && !target.value) target.value = site.value;
      selectTab(go.dataset.goto);
    }
  });

  var quick = document.getElementById("quick-form");
  if (quick) quick.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var out = document.getElementById("quick-result");
    busy(quick, true, "Asking the AI…");
    out.innerHTML = '<p class="hint">Asking the AI and reading its sources…</p>';
    try {
      renderAnswer(out, await postJSON("/api/check", { site: quick.site.value, question: quick.question.value }));
    } catch (err) { showError(out, err); }
    busy(quick, false);
  });

  var paste = document.getElementById("paste-form");
  if (paste) paste.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var out = document.getElementById("paste-result");
    busy(paste, true);
    try {
      renderAnswer(out, await postJSON("/api/paste", {
        site: paste.site.value, text: paste.text.value,
        question: paste.question.value, engine_label: paste.engine_label.value,
      }));
    } catch (err) { showError(out, err); }
    busy(paste, false);
  });

  var report = document.getElementById("report-form");
  if (report) {
    var suggest = document.getElementById("suggest-btn");
    suggest.addEventListener("click", async function () {
      var out = document.getElementById("report-status");
      suggest.disabled = true; suggest.textContent = "Reading your site…";
      try {
        var data = await postJSON("/api/questions", { site: report.site.value });
        if (!data.questions.length) throw new Error("Couldn't tell what the site offers. Type a few questions instead.");
        report.questions.value = data.questions.join("\n");
        out.innerHTML = "";
      } catch (err) { showError(out, err); }
      suggest.disabled = false; suggest.textContent = "Suggest questions";
    });

    report.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      var out = document.getElementById("report-status");
      busy(report, true, "Starting…");
      var questions = report.questions.value.split("\n").map(function (q) { return q.trim(); }).filter(Boolean);
      var competitors = report.competitors.value.split(",").map(function (c) { return c.trim(); }).filter(Boolean);
      try {
        var data = await postJSON("/api/reports", { site: report.site.value, questions: questions, competitors: competitors });
        window.location.href = data.url;
      } catch (err) { showError(out, err); busy(report, false); }
    });
  }

  // Report page: poll until finished, then reload to show the full report.
  var progress = document.getElementById("progress");
  if (progress) {
    var id = progress.dataset.reportId;
    var timer = setInterval(async function () {
      try {
        var resp = await fetch("/api/reports/" + encodeURIComponent(id));
        if (!resp.ok) return;
        var r = await resp.json();
        document.getElementById("progress-msg").textContent = r.message || "Working…";
        document.getElementById("progress-bar").style.width = Math.round(r.progress * 100) + "%";
        if (r.status === "done" || r.status === "error") { clearInterval(timer); window.location.reload(); }
      } catch (e) { /* network blip: try again next tick */ }
    }, 1500);
  }
})();
