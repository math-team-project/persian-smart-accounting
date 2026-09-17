/**
 * منطق صفحه‌ی کارگاه چک‌لیست حسابرسی (آپلود -> پردازش -> نتایج).
 *
 * جاوااسکریپت خالص (بدون فریم‌ورک)، طراحی‌شده برای شکست‌های واضح و پیام خطای
 * فارسی به کاربر (هیچ خطایی نباید بی‌صدا نادیده گرفته شود) -- با استفاده از
 * ``window.psaShowToast`` که در components/toast.html تعریف شده است.
 *
 * نکته‌ی طراحی: وضعیت‌های بصری با کلاس‌های معنایی (``is-active``، ``is-false``،
 * ...) بیان می‌شوند که در ``web/static/css/app.css`` تعریف شده‌اند؛ بنابراین
 * ظاهر کارت‌ها/فیلترها کاملاً در CSS متمرکز است و این فایل فقط منطق را
 * مدیریت می‌کند.
 */
(function () {
  "use strict";

  /* آیکون‌های درون‌خطی (هم‌سبک با api/utils/icons.py) -- این فایل استاتیک است و
     به تابع Jinja ``icon()`` دسترسی ندارد، پس SVG مورد نیاز این‌جا تکرار شده است. */
  var ICONS = {
    check: '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    x: '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    alert: '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    eye: '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
    chevron: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>'
  };

  /* هر وضعیت: کلاس ظاهری کارت + برچسب + کلاس نشان + آیکون */
  var STATUS_META = {
    TRUE: { label: "تطابق دارد", cls: "is-true", badge: "psa-badge psa-badge-success", icon: ICONS.check },
    FALSE: { label: "عدم تطابق", cls: "is-false", badge: "psa-badge psa-badge-danger", icon: ICONS.x },
    ERROR: { label: "خطای پردازش", cls: "is-error", badge: "psa-badge psa-badge-warning", icon: ICONS.alert },
    MANUAL: { label: "نیازمند بررسی دستی", cls: "is-manual", badge: "psa-badge psa-badge-neutral", icon: ICONS.eye }
  };

  var POLL_INTERVAL_MS = 1500;

  /* کلید نگه‌داری شناسه‌ی آخرین job در مرورگر.
     job ها در حافظه‌ی سرور باقی می‌مانند (``api/jobs/job_manager.py``)، بنابراین
     کافی است شناسه‌ی job را در مرورگر به خاطر بسپاریم تا کاربر بتواند بین
     کارگاه‌ها جابه‌جا شود (یا صفحه را دوباره بارگذاری کند) و پردازش در حال
     اجرا/تمام‌شده‌ی خود را از دست ندهد. */
  var STORAGE_KEY = "psa.checklist.jobId";

  var state = {
    jobId: null,
    pollTimer: null,
    items: [],
    activeFilter: "ALL",
    searchTerm: "",
    step: null
  };

  /* ------------------------------------------------------------------
     نگه‌داری/بازیابی شناسه‌ی job (با محافظت در برابر دسترسی مسدود حافظه‌ی محلی)
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
      // در حالت ناشناس/مسدود، فقط خاصیت «ماندگاری» از دست می‌رود؛ پردازش درست کار می‌کند.
    }
  }

  function clearStoredJobId() {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      // نادیده‌گرفتنی است.
    }
  }

  function showStep(stepIndex) {
    document.getElementById("psa-step-upload").classList.toggle("hidden", stepIndex !== 0);
    document.getElementById("psa-step-processing").classList.toggle("hidden", stepIndex !== 1);
    document.getElementById("psa-step-results").classList.toggle("hidden", stepIndex !== 2);
    if (typeof window.psaSetStep === "function") window.psaSetStep(stepIndex);
    if (state.step !== stepIndex) {
      state.step = stepIndex;
      // رفتار نرم اسکرول از CSS می‌آید تا تنظیم «کاهش حرکت» کاربر رعایت شود.
      window.scrollTo({ top: 0 });
    }
  }

  /** بازگشت به مرحله‌ی آپلود برای شروع یک پردازش تازه (job قبلی در سرور دست‌نخورده می‌ماند). */
  function resetWorkspace() {
    stopPolling();
    state.jobId = null;
    state.items = [];
    state.activeFilter = "ALL";
    state.searchTerm = "";
    state.step = null;
    clearStoredJobId();

    var searchInput = document.getElementById("psa-search-input");
    if (searchInput) searchInput.value = "";
    document.querySelectorAll(".psa-filter-btn").forEach(function (el) {
      var isAll = el.dataset.filter === "ALL";
      el.classList.toggle("is-active", isAll);
      el.setAttribute("aria-pressed", isAll ? "true" : "false");
    });
    document.getElementById("psa-results-list").innerHTML = "";
    document.getElementById("psa-results-empty").classList.add("hidden");

    if (typeof window.psaSetProgress === "function") {
      window.psaSetProgress(0, "در حال آماده‌سازی...", []);
    }
    showStep(0);
    window.psaHideToast();
  }

  /**
   * اگر کاربر قبلاً پردازشی را شروع کرده باشد (و در این فاصله به کارگاه دیگری
   * رفته یا صفحه را دوباره بارگذاری کرده باشد)، همان پردازش بازیابی می‌شود:
   * پیگیری ادامه می‌یابد، نتیجه‌ی تمام‌شده نمایش داده می‌شود و خطای قبلی گزارش
   * می‌گردد. اگر job دیگر روی سرور موجود نباشد (مثلاً بعد از ری‌استارت سرور)،
   * بی‌سروصدا به مرحله‌ی آپلود برمی‌گردیم.
   */
  async function restoreJob() {
    try {
      const response = await fetch("/api/checklist/jobs/" + encodeURIComponent(state.jobId));
      if (!response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      const job = await response.json();

      if (job.status === "done") {
        await loadResults();
        return;
      }
      if (job.status === "error") {
        clearStoredJobId();
        state.jobId = null;
        showStep(0);
        window.psaShowToast(job.error || "پردازش قبلی با خطا متوقف شد.", "error");
        return;
      }

      // pending / running -- نمایش وضعیت فعلی و ادامه‌ی پیگیری
      showStep(1);
      window.psaSetProgress(job.progress, job.stage, job.logs);
      startPolling();
    } catch (err) {
      // job ناشناخته است (سرور ری‌استارت شده یا منقضی شده) -- از ابتدا شروع می‌کنیم.
      clearStoredJobId();
      state.jobId = null;
      showStep(0);
    }
  }

  async function parseErrorDetail(response) {
    try {
      const data = await response.json();
      if (data && data.detail) return data.detail;
    } catch (err) {
      // پاسخ JSON نبود؛ متن خام را برمی‌گردانیم.
    }
    return "خطای غیرمنتظره‌ای از سرور دریافت شد (کد " + response.status + ").";
  }

  async function submitForm(event) {
    event.preventDefault();
    const form = document.getElementById("psa-checklist-form");
    const submitBtn = document.getElementById("psa-submit-btn");
    const formData = new FormData(form);

    submitBtn.disabled = true;
    try {
      const response = await fetch("/api/checklist/jobs", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      const data = await response.json();
      state.jobId = data.job_id;
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
      const response = await fetch("/api/checklist/jobs/" + encodeURIComponent(state.jobId));
      if (!response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      const job = await response.json();
      window.psaSetProgress(job.progress, job.stage, job.logs);

      if (job.status === "done") {
        stopPolling();
        await loadResults();
      } else if (job.status === "error") {
        stopPolling();
        window.psaShowToast(
          job.error || "پردازش با خطا متوقف شد.",
          "error"
        );
      }
    } catch (err) {
      stopPolling();
      window.psaShowToast(
        "دریافت وضعیت پردازش ناموفق بود: " + (err && err.message ? err.message : String(err)) +
          " — لطفاً صفحه را دوباره بارگذاری کنید.",
        "error"
      );
    }
  }

  async function loadResults() {
    try {
      const response = await fetch("/api/checklist/jobs/" + encodeURIComponent(state.jobId) + "/results");
      if (!response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      const data = await response.json();
      renderResults(data);
      showStep(2);
    } catch (err) {
      window.psaShowToast(
        "دریافت نتایج ناموفق بود: " + (err && err.message ? err.message : String(err)),
        "error"
      );
    }
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function renderResults(data) {
    state.items = data.items || [];
    var summary = data.summary || {};

    setText(
      "psa-summary-headline",
      (summary.false_count || 0) + " مورد نامنطبق از " + (summary.total || 0) + " بررسی"
    );

    var compliance = Math.round(summary.compliance_rate || 0);
    setText("psa-summary-compliance", compliance + "٪");
    var meter = document.getElementById("psa-compliance-meter");
    if (meter) meter.style.width = Math.max(0, Math.min(100, compliance)) + "%";

    setText("psa-stat-total", String(summary.total || 0));
    setText("psa-stat-true", String(summary.true_count || 0));
    setText("psa-stat-false", String(summary.false_count || 0));
    setText("psa-stat-error", String(summary.error_count || 0));
    setText("psa-stat-manual", String(summary.manual_count || 0));

    var downloadLink = document.getElementById("psa-download-report");
    var unavailableLabel = document.getElementById("psa-report-unavailable");
    if (data.report_ready) {
      downloadLink.href = "/api/checklist/jobs/" + encodeURIComponent(state.jobId) + "/report";
      downloadLink.classList.remove("hidden");
      unavailableLabel.classList.add("hidden");
    } else {
      downloadLink.classList.add("hidden");
      unavailableLabel.classList.remove("hidden");
      if (data.committee_report_error) {
        unavailableLabel.textContent = "خطا در تولید گزارش: " + data.committee_report_error;
      }
    }

    var warningsBox = document.getElementById("psa-results-warnings");
    if (data.warnings && data.warnings.length > 0) {
      warningsBox.innerHTML =
        '<span class="shrink-0">' + ICONS.alert + "</span><span>" +
        data.warnings.map(function (w) { return escapeHtml(w); }).join("<br>") +
        "</span>";
      warningsBox.classList.remove("hidden");
    } else {
      warningsBox.classList.add("hidden");
    }

    renderItemList();
  }

  function renderItemList() {
    var container = document.getElementById("psa-results-list");
    var emptyLabel = document.getElementById("psa-results-empty");
    var term = state.searchTerm.trim().toLowerCase();

    var filtered = state.items.filter(function (item) {
      if (state.activeFilter !== "ALL" && item.status !== state.activeFilter) return false;
      if (!term) return true;
      return (
        (item.question_text || "").toLowerCase().includes(term) ||
        (item.question_id || "").toLowerCase().includes(term) ||
        (item.message || "").toLowerCase().includes(term)
      );
    });

    setText(
      "psa-results-count",
      "نمایش " + filtered.length.toLocaleString("fa-IR") + " از " +
        state.items.length.toLocaleString("fa-IR") + " مورد"
    );

    if (filtered.length === 0) {
      container.innerHTML = "";
      emptyLabel.classList.remove("hidden");
      return;
    }
    emptyLabel.classList.add("hidden");

    container.innerHTML = filtered.map(renderItemCard).join("");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /**
   * نشان شماره‌ی پرسش (``question_id`` مثل Q12).
   * شماره‌ی پرسش عیناً همان شناسه‌ی چک‌لیست منبع و گزارش Word است تا کاربر
   * بتواند هر مورد را در سند اصلی پیدا کند؛ به همین دلیل با ``dir="ltr"`` و
   * ارقام لاتین نمایش داده می‌شود (نه ارقام فارسی).
   */
  function questionIdChip(questionId) {
    var id = String(questionId == null ? "" : questionId).trim();
    if (!id) return "";
    return (
      '<span class="psa-qid shrink-0" dir="ltr" aria-label="شمارهٔ پرسش ' +
      escapeHtml(id) + '" title="شمارهٔ پرسش در چک‌لیست: ' + escapeHtml(id) + '">' +
      escapeHtml(id) + "</span>"
    );
  }

  function renderItemCard(item) {
    var meta = STATUS_META[item.status] || STATUS_META.MANUAL;

    var extractedRows = "";
    if (item.extracted_data && item.extracted_data.length > 0) {
      extractedRows = item.extracted_data
        .map(
          function (row) {
            return (
              '<tr><td class="psa-table-num">' + escapeHtml(row["متغیر"]) + "</td>" +
              '<td class="psa-num">' + escapeHtml(row["مقدار استخراج‌شده"]) + "</td></tr>"
            );
          }
        )
        .join("");
    }

    var conditionRows = "";
    if (item.condition_breakdown && item.condition_breakdown.length > 0) {
      conditionRows = item.condition_breakdown
        .map(
          function (cond) {
            var passed = cond.result === "PASSED";
            return (
              '<li class="psa-condition ' + (passed ? "is-passed" : "is-failed") + '">' +
              '<span class="psa-condition-mark" aria-hidden="true">' + (passed ? "✓" : "✕") + "</span>" +
              '<span><span class="sr-only">' + (passed ? "شرط برقرار است: " : "شرط برقرار نیست: ") + "</span>" +
              escapeHtml(cond.condition) + "</span></li>"
            );
          }
        )
        .join("");
    }

    return (
      '<details class="psa-result-item psa-item ' + meta.cls + '">' +
      '<summary class="flex items-center justify-between gap-3">' +
      '<div class="flex min-w-0 items-center gap-2">' +
      '<span class="psa-chevron shrink-0 text-slate-400" aria-hidden="true">' + ICONS.chevron + "</span>" +
      questionIdChip(item.question_id) +
      '<span class="truncate text-sm font-semibold text-slate-800">' +
      "<span class=\"sr-only\">پرسش </span>" +
      escapeHtml(item.question_text) + "</span>" +
      "</div>" +
      '<span class="shrink-0 ' + meta.badge + '">' + meta.icon + "<span>" + meta.label + "</span></span>" +
      "</summary>" +
      '<div class="psa-result-body space-y-3 text-sm text-slate-600">' +
      (item.message ? "<p>" + escapeHtml(item.message) + "</p>" : "") +
      (conditionRows ? '<ul class="space-y-1">' + conditionRows + "</ul>" : "") +
      (extractedRows
        ? '<div class="psa-table-wrap"><table class="psa-table-inline"><tbody>' + extractedRows + "</tbody></table></div>"
        : "") +
      "</div>" +
      "</details>"
    );
  }

  function bindResultFilters() {
    document.getElementById("psa-status-filters").addEventListener("click", function (event) {
      var btn = event.target.closest(".psa-filter-btn");
      if (!btn) return;
      state.activeFilter = btn.dataset.filter;
      document.querySelectorAll(".psa-filter-btn").forEach(function (el) {
        el.classList.toggle("is-active", el === btn);
        el.setAttribute("aria-pressed", el === btn ? "true" : "false");
      });
      renderItemList();
    });

    document.getElementById("psa-search-input").addEventListener("input", function (event) {
      state.searchTerm = event.target.value || "";
      renderItemList();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("psa-checklist-form");
    if (!form) return;
    form.addEventListener("submit", submitForm);
    bindResultFilters();

    // «شروع پردازش جدید» -- از مرحله‌ی پردازش یا نتایج به آپلود برمی‌گردد و
    // شناسه‌ی job ذخیره‌شده را پاک می‌کند تا دفعه‌ی بعد کارگاه تازه باز شود.
    var restartBtn = document.getElementById("psa-restart-btn");
    if (restartBtn) restartBtn.addEventListener("click", resetWorkspace);
    var restartBtnResults = document.getElementById("psa-restart-btn-results");
    if (restartBtnResults) restartBtnResults.addEventListener("click", resetWorkspace);

    // اگر پردازشی از قبل شروع شده باشد، همان بازیابی می‌شود؛ در غیر این صورت
    // کارگاه از مرحله‌ی آپلود شروع می‌کند.
    var storedJobId = readStoredJobId();
    if (storedJobId) {
      state.jobId = storedJobId;
      restoreJob();
    } else {
      showStep(0);
    }
  });
})();
