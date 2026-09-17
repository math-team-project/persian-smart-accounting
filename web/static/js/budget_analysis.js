/**
 * منطق صفحه‌ی کارگاه «تحلیل بودجه» (آپلود -> پردازش -> نتیجه).
 *
 * ساختار عیناً همان الگوی ``checklist.js``/``audit_summary.js`` است: وضعیت job
 * سمت سرور در حافظه می‌ماند و این فایل فقط با API همین کارگاه (که آدرسش از
 * رجیستری تزریق می‌شود) حرف می‌زند -- هیچ آدرسی این‌جا هاردکد نیست.
 *
 * نکته‌ی نمایشی: این صفحه فقط *خلاصه‌ی مرورگری* نتیجه را نشان می‌دهد؛ سند رسمی
 * همان فایل Word قابل دانلود است.
 */
(function () {
  "use strict";

  var ICONS = {
    alert:
      '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    chevron:
      '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>'
  };

  var POLL_INTERVAL_MS = 1500;

  var WORKSHOP_CONFIG = window.PSA_WORKSHOP || {};
  var API_BASE = WORKSHOP_CONFIG.apiBase || "/api/budget-analysis";
  var STORAGE_KEY = WORKSHOP_CONFIG.storageKey || "psa.budget.jobId";

  var state = { jobId: null, runId: null, pollTimer: null, step: null };

  /* ------------------------------------------------------------------
     نگه‌داری شناسهٔ job (با محافظت در برابر دسترسی مسدود حافظهٔ محلی)
     ------------------------------------------------------------------ */
  function readStoredJobId() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (err) {
      return null;
    }
  }
  function storeJobId(jobId) {
    try {
      window.localStorage.setItem(STORAGE_KEY, jobId);
    } catch (err) {
      /* ماندگاری از دست می‌رود؛ پردازش بی‌مشکل ادامه دارد. */
    }
  }
  function clearStoredJobId() {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      /* نادیده‌گرفتنی است. */
    }
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /** نمایش عدد با ارقام فارسی؛ ارقام لاتین ورودی مدل هم فارسی می‌شوند. */
  function faDigits(value) {
    var text = value === null || value === undefined ? "" : String(value);
    if (text === "") return "—";
    return text.replace(/[0-9]/g, function (digit) {
      return "۰۱۲۳۴۵۶۷۸۹".charAt(Number(digit));
    });
  }

  function faNumber(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number" && isFinite(value)) {
      var text = Math.abs(value) >= 1000 ? value.toLocaleString("fa-IR") : String(value);
      return text.replace(/[0-9]/g, function (digit) {
        return "۰۱۲۳۴۵۶۷۸۹".charAt(Number(digit));
      });
    }
    return faDigits(value);
  }

  function showStep(stepIndex) {
    var upload = document.getElementById("psa-step-upload");
    var processing = document.getElementById("psa-step-processing");
    var results = document.getElementById("psa-step-results");
    if (upload) upload.classList.toggle("hidden", stepIndex !== 0);
    if (processing) processing.classList.toggle("hidden", stepIndex !== 1);
    if (results) results.classList.toggle("hidden", stepIndex !== 2);
    if (typeof window.psaSetStep === "function") window.psaSetStep(stepIndex);
    if (state.step !== stepIndex) {
      state.step = stepIndex;
      window.scrollTo({ top: 0 });
    }
  }

  function resetWorkspace() {
    stopPolling();
    state.jobId = null;
    state.runId = null;
    state.step = null;
    clearStoredJobId();
    if (typeof window.psaSetProgress === "function") {
      window.psaSetProgress(0, "در حال آماده‌سازی…", []);
    }
    showStep(0);
    window.psaHideToast();
  }

  async function parseErrorDetail(response) {
    try {
      var data = await response.json();
      if (data && data.detail) return data.detail;
    } catch (err) {
      /* پاسخ JSON نبود. */
    }
    return "خطای غیرمنتظره‌ای از سرور دریافت شد (کد " + response.status + ").";
  }

  async function restoreJob() {
    try {
      var response = await fetch(API_BASE + "/jobs/" + encodeURIComponent(state.jobId));
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      var job = await response.json();
      if (job.run_id) state.runId = job.run_id;

      if (job.status === "done") {
        await loadResults();
        return;
      }
      if (job.status === "error") {
        clearStoredJobId();
        state.jobId = null;
        state.runId = null;
        showStep(0);
        window.psaShowToast(job.error || "پردازش قبلی با خطا متوقف شد.", "error");
        return;
      }
      showStep(1);
      window.psaSetProgress(job.progress, job.stage, job.logs);
      startPolling();
    } catch (err) {
      clearStoredJobId();
      state.jobId = null;
      state.runId = null;
      showStep(0);
    }
  }

  async function submitForm(event) {
    event.preventDefault();
    var form = document.getElementById("psa-budget-form");
    var submitBtn = document.getElementById("psa-submit-btn");
    var formData = new FormData(form);

    submitBtn.disabled = true;
    try {
      var response = await fetch(API_BASE + "/jobs", { method: "POST", body: formData });
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      var data = await response.json();
      state.jobId = data.job_id;
      state.runId = data.run_id || null;
      storeJobId(state.jobId);
      showStep(1);
      startPolling();
    } catch (err) {
      window.psaShowToast(
        "شروع پردازش ناموفق بود: " + (err && err.message ? err.message : String(err)),
        "error"
      );
    } finally {
      submitBtn.disabled = false;
    }
  }

  function startPolling() {
    stopPolling();
    pollOnce();
    state.pollTimer = window.setInterval(pollOnce, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function pollOnce() {
    if (!state.jobId) return;
    try {
      var response = await fetch(API_BASE + "/jobs/" + encodeURIComponent(state.jobId));
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      var job = await response.json();
      if (job.run_id) state.runId = job.run_id;
      window.psaSetProgress(job.progress, job.stage, job.logs);

      if (job.status === "done") {
        stopPolling();
        await loadResults();
      } else if (job.status === "error") {
        stopPolling();
        window.psaShowToast(job.error || "پردازش با خطا متوقف شد.", "error");
      }
    } catch (err) {
      stopPolling();
      window.psaShowToast(
        "دریافت وضعیت پردازش ناموفق بود: " +
          (err && err.message ? err.message : String(err)) +
          " — لطفاً صفحه را دوباره بارگذاری کنید.",
        "error"
      );
    }
  }

  async function loadResults() {
    try {
      var response = await fetch(
        API_BASE + "/jobs/" + encodeURIComponent(state.jobId) + "/results"
      );
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      renderResults(await response.json());
      showStep(2);
    } catch (err) {
      window.psaShowToast(
        "دریافت نتایج ناموفق بود: " + (err && err.message ? err.message : String(err)),
        "error"
      );
    }
  }

  /* ------------------------------------------------------------------
     رندر نتیجه
     ------------------------------------------------------------------ */
  function setHtml(id, html) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }

  function renderResults(data) {
    var meta = document.getElementById("psa-result-meta");
    if (meta) {
      var parts = [];
      if (data.organization) parts.push(data.organization);
      if (data.current_year) {
        parts.push("سال مورد بررسی " + faDigits(data.current_year) + " / سال پایه " + faDigits(data.base_year));
      }
      if (data.unit) parts.push("واحد مبالغ: " + data.unit);
      if (data.source_documents && data.source_documents.length) {
        parts.push("اسناد: " + data.source_documents.join(" ، "));
      }
      meta.textContent = parts.join("  •  ");
    }

    var downloadLink = document.getElementById("psa-download-report");
    var unavailable = document.getElementById("psa-report-unavailable");
    if (data.report_ready && state.runId) {
      // دانلود از مسیر «اجرا» انجام می‌شود تا پس از خروج job از حافظه‌ی سرور هم
      // فایل ذخیره‌شده در تاریخچه قابل دریافت باشد.
      downloadLink.href = API_BASE + "/runs/" + encodeURIComponent(state.runId) + "/download";
      downloadLink.classList.remove("hidden");
      unavailable.classList.add("hidden");
    } else {
      downloadLink.classList.add("hidden");
      unavailable.classList.remove("hidden");
      if (data.report_error) unavailable.textContent = "خطا: " + data.report_error;
    }

    var warningsBox = document.getElementById("psa-results-warnings");
    if (warningsBox) {
      if (data.warnings && data.warnings.length) {
        warningsBox.innerHTML =
          '<span class="shrink-0">' + ICONS.alert + "</span><span>" +
          data.warnings.map(escapeHtml).join("<br>") +
          "</span>";
        warningsBox.classList.remove("hidden");
      } else {
        warningsBox.classList.add("hidden");
      }
    }

    renderStats(data);
    renderExecutiveSummary(data);
    renderAxisTable(data);
    renderTopFindings(data);
    renderMatrixTable(data);
    renderFindings(data);
    renderRisks(data);
    renderDecisions(data);
    renderClarifications(data);
    renderClosingNotes(data);
  }

  function statCard(label, value, hint, cls) {
    return (
      '<div class="psa-card p-4">' +
      '<p class="psa-section-hint">' + escapeHtml(label) + "</p>" +
      '<p class="mt-1 text-2xl font-bold ' + (cls || "text-slate-900") + '">' + value + "</p>" +
      '<p class="psa-help">' + escapeHtml(hint || "") + "</p>" +
      "</div>"
    );
  }

  function renderStats(data) {
    var missing = (data.status_summary && data.status_summary["فاقد داده کافی"]) || 0;
    setHtml(
      "psa-result-stats",
      statCard("معیارهای ارزیابی‌شده", faNumber(data.matrix_total), "کل ردیف‌های ماتریس خطادهی") +
        statCard("موارد دارای انحراف", faNumber(data.deviation_total), "هشدار، مغایرت یا ریسک", "text-rose-700") +
        statCard("یافته‌های بااهمیت", faNumber((data.findings || []).length), "یافته‌های مرجع گزارش") +
        statCard("معیارهای فاقد داده", faNumber(missing), "داده موردنیاز در اسناد نبوده است")
    );
  }

  function renderExecutiveSummary(data) {
    var summary = data.executive_summary || {};
    var keys = Object.keys(summary);
    if (!keys.length) {
      setHtml("psa-executive-summary", '<p class="psa-help">خلاصه مدیریتی ثبت نشده است.</p>');
      return;
    }
    setHtml(
      "psa-executive-summary",
      keys
        .map(function (key) {
          return (
            "<p><strong>" + escapeHtml(key) + ":</strong> " + escapeHtml(summary[key]) + "</p>"
          );
        })
        .join("")
    );
  }

  function table(headers, rows, options) {
    if (!rows.length) {
      return '<p class="psa-help">' + escapeHtml((options && options.empty) || "موردی ثبت نشده است.") + "</p>";
    }
    var head = headers.map(function (h) {
      return "<th>" + escapeHtml(h) + "</th>";
    }).join("");
    var body = rows
      .map(function (row) {
        return (
          "<tr>" +
          row
            .map(function (cell) {
              return "<td>" + (cell === null || cell === undefined ? "—" : cell) + "</td>";
            })
            .join("") +
          "</tr>"
        );
      })
      .join("");
    return (
      '<table class="psa-table"><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table>"
    );
  }

  /** نشان وضعیت: همیشه متن + کلاس رنگی (رنگ هرگز تنها نشانگر نیست). */
  function statusBadge(status) {
    return '<span class="psa-badge psa-badge-neutral">' + escapeHtml(status || "—") + "</span>";
  }

  function renderAxisTable(data) {
    var rows = (data.axis_dashboard || []).map(function (row) {
      return [
        escapeHtml(row.axis),
        statusBadge(row.status),
        escapeHtml(row.importance),
        faNumber(row.findings_count),
        escapeHtml(row.management_summary)
      ];
    });
    setHtml(
      "psa-axis-table",
      table(["محور پایش", "وضعیت", "اهمیت", "تعداد یافته", "جمع‌بندی مدیریتی"], rows, {
        empty: "وضعیت محوری ثبت نشده است."
      })
    );
  }

  function renderTopFindings(data) {
    var rows = (data.top_findings || []).map(function (finding) {
      return [
        faNumber(finding.rank),
        escapeHtml(finding.axis),
        escapeHtml(finding.criterion),
        escapeHtml(finding.location),
        faNumber(finding.observed_value),
        faNumber(finding.reference_value),
        faNumber(finding.deviation),
        escapeHtml(finding.deviation_unit || "—"),
        escapeHtml(finding.importance),
        escapeHtml(finding.management_message)
      ];
    });
    setHtml(
      "psa-top-findings",
      table(
        [
          "رتبه",
          "محور",
          "معیار",
          "محل",
          "مقدار مشاهده‌شده",
          "مقدار مرجع",
          "انحراف",
          "واحد انحراف",
          "اهمیت",
          "پیام مدیریتی"
        ],
        rows,
        { empty: "انحراف عددی قابل‌توجهی ثبت نشده است." }
      )
    );
  }

  function renderMatrixTable(data) {
    var rows = (data.deviating_rows || []).map(function (row) {
      return [
        escapeHtml(row.axis),
        escapeHtml(row.criterion),
        escapeHtml(row.location),
        faNumber(row.base_value),
        faNumber(row.current_value),
        faNumber(row.threshold),
        faNumber(row.absolute_deviation),
        faNumber(row.percentage_deviation),
        statusBadge(row.status),
        escapeHtml(row.importance)
      ];
    });
    var counter = document.getElementById("psa-matrix-count");
    if (counter) {
      counter.textContent =
        faNumber(data.deviation_total) + " از " + faNumber(data.matrix_total) + " معیار";
    }
    setHtml(
      "psa-matrix-table",
      table(
        [
          "محور",
          "معیار",
          "محل مشاهده",
          "مقدار مبنا",
          "مقدار جاری",
          "حد مجاز",
          "انحراف مطلق",
          "انحراف نسبت به حد",
          "وضعیت",
          "اهمیت"
        ],
        rows,
        { empty: "مورد دارای انحراف ثبت نشده است." }
      )
    );
  }

  function renderFindings(data) {
    var findings = data.findings || [];
    if (!findings.length) {
      setHtml("psa-findings", '<p class="psa-help">یافته‌ی بااهمیتی ثبت نشده است.</p>');
      return;
    }
    setHtml(
      "psa-findings",
      findings
        .map(function (finding) {
          var number = finding.number === null || finding.number === undefined ? "" : faDigits(finding.number) + " ـ ";
          var blocks = [
            ["موضوع", finding.subject],
            ["نتیجه ارزیابی", finding.assessment],
            ["اهمیت مدیریتی", finding.management_importance],
            ["ریسک و آثار احتمالی", finding.risk],
            ["اقدام پیشنهادی", finding.action]
          ]
            .filter(function (pair) {
              return pair[1] && String(pair[1]).trim();
            })
            .map(function (pair) {
              return (
                "<p><strong>" + escapeHtml(pair[0]) + ":</strong> " + escapeHtml(pair[1]) + "</p>"
              );
            })
            .join("");
          return (
            '<details class="psa-result-item">' +
            '<summary class="flex items-center justify-between gap-3">' +
            '<div class="flex min-w-0 items-center gap-2">' +
            '<span class="psa-chevron shrink-0 text-slate-400" aria-hidden="true">' + ICONS.chevron + "</span>" +
            '<span class="truncate text-sm font-semibold text-slate-800">' +
            escapeHtml(number + (finding.title || "")) +
            "</span></div></summary>" +
            '<div class="psa-result-body space-y-1 text-sm text-slate-600">' + blocks + "</div>" +
            "</details>"
          );
        })
        .join("")
    );
  }

  function renderRisks(data) {
    var rows = (data.risks || []).map(function (risk) {
      return [
        escapeHtml(risk.description),
        faNumber(risk.approximate_amount),
        escapeHtml(risk.importance),
        escapeHtml(risk.nature)
      ];
    });
    setHtml(
      "psa-risks",
      table(["شرح ریسک", "مبلغ تقریبی", "درجه اهمیت", "ماهیت ریسک"], rows, {
        empty: "ریسکی ثبت نشده است."
      })
    );
  }

  function renderDecisions(data) {
    var items = data.decisions || [];
    if (!items.length) {
      setHtml("psa-decisions", '<p class="psa-help">موردی برای تصمیم‌گیری ثبت نشده است.</p>');
      return;
    }
    setHtml(
      "psa-decisions",
      items
        .map(function (item, index) {
          return (
            '<div class="psa-card p-4">' +
            '<p class="text-sm font-bold text-slate-800">' +
            faNumber(index + 1) + ". " + escapeHtml(item.subject) +
            "</p>" +
            (item.question
              ? '<p class="mt-1 text-sm text-slate-600"><strong>پرسش پیشنهادی از مدیریت:</strong> ' +
                escapeHtml(item.question) + "</p>"
              : "") +
            (item.proposed_action
              ? '<p class="mt-1 text-sm text-slate-600"><strong>پیشنهاد اقدام / تصمیم:</strong> ' +
                escapeHtml(item.proposed_action) + "</p>"
              : "") +
            "</div>"
          );
        })
        .join("")
    );
  }

  function renderClarifications(data) {
    var items = data.clarifications || [];
    if (!items.length) {
      setHtml("psa-clarifications", '<p class="psa-help">مورد نیازمند شفاف‌سازی ثبت نشده است.</p>');
      return;
    }
    setHtml(
      "psa-clarifications",
      items
        .map(function (item) {
          return (
            '<div class="psa-card p-4">' +
            '<p class="text-sm font-bold text-slate-800">' + escapeHtml(item.subject) + "</p>" +
            '<p class="mt-1 text-sm text-slate-600"><strong>داده موجود:</strong> ' +
            escapeHtml(item.available_data) + "</p>" +
            '<p class="mt-1 text-sm text-slate-600"><strong>داده مفقود یا متعارض:</strong> ' +
            escapeHtml(item.missing_or_conflicting_data) + "</p>" +
            '<p class="mt-1 text-sm text-slate-600"><strong>محل مشاهده:</strong> ' +
            escapeHtml(item.location) + "</p>" +
            '<p class="mt-1 text-sm text-slate-600"><strong>علت نیاز به شفاف‌سازی:</strong> ' +
            escapeHtml(item.reason) + "</p>" +
            '<p class="mt-1 text-sm text-slate-600"><strong>مستند موردنیاز:</strong> ' +
            escapeHtml(item.required_document) + "</p>" +
            "</div>"
          );
        })
        .join("")
    );
  }

  function renderClosingNotes(data) {
    var notes = data.closing_notes || [];
    setHtml(
      "psa-closing-notes",
      notes.length
        ? notes.map(function (note) { return "<li>" + escapeHtml(note) + "</li>"; }).join("")
        : '<li class="psa-help">نکته‌ی تکمیلی ثبت نشده است.</li>'
    );
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("psa-budget-form");
    if (!form) return;
    form.addEventListener("submit", submitForm);

    var restart = document.getElementById("psa-restart-btn");
    if (restart) restart.addEventListener("click", resetWorkspace);
    var restartResults = document.getElementById("psa-restart-btn-results");
    if (restartResults) restartResults.addEventListener("click", resetWorkspace);

    var storedJobId = readStoredJobId();
    if (storedJobId) {
      state.jobId = storedJobId;
      restoreJob();
    } else {
      showStep(0);
    }
  });
})();
