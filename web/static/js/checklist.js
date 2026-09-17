/**
 * منطق صفحه‌ی کارگاه چک‌لیست حسابرسی (آپلود -> پردازش -> نتایج).
 *
 * جاوااسکریپت خالص (بدون فریم‌ورک)، طراحی‌شده برای شکست‌های واضح و پیام خطای
 * فارسی به کاربر (هیچ خطایی نباید بی‌صدا نادیده گرفته شود) -- با استفاده از
 * ``window.psaShowToast`` که در components/toast.html تعریف شده است.
 */
(function () {
  "use strict";

  const STATUS_META = {
    TRUE: { label: "تطابق دارد", classes: "border-slate-200 bg-white", badge: "bg-emerald-50 text-emerald-700" },
    FALSE: { label: "عدم تطابق", classes: "border-red-200 bg-red-50", badge: "bg-red-100 text-red-700" },
    ERROR: { label: "خطای پردازش", classes: "border-amber-200 bg-amber-50", badge: "bg-amber-100 text-amber-700" },
    MANUAL: { label: "نیازمند بررسی دستی", classes: "border-slate-200 bg-white", badge: "bg-slate-100 text-slate-600" },
  };

  const POLL_INTERVAL_MS = 1500;

  const state = {
    jobId: null,
    pollTimer: null,
    items: [],
    activeFilter: "ALL",
    searchTerm: "",
  };

  function showStep(stepIndex) {
    document.getElementById("psa-step-upload").classList.toggle("hidden", stepIndex !== 0);
    document.getElementById("psa-step-processing").classList.toggle("hidden", stepIndex !== 1);
    document.getElementById("psa-step-results").classList.toggle("hidden", stepIndex !== 2);
    if (typeof window.psaSetStep === "function") window.psaSetStep(stepIndex);
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

  function renderResults(data) {
    state.items = data.items || [];

    const headline = data.summary.false_count + " مورد نامنطبق از " + data.summary.total + " بررسی";
    document.getElementById("psa-summary-headline").textContent = headline;
    document.getElementById("psa-summary-compliance").textContent =
      Math.round(data.summary.compliance_rate) + "٪";

    const downloadLink = document.getElementById("psa-download-report");
    const unavailableLabel = document.getElementById("psa-report-unavailable");
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

    const warningsBox = document.getElementById("psa-results-warnings");
    if (data.warnings && data.warnings.length > 0) {
      warningsBox.innerHTML = data.warnings
        .map((w) => "<div>&#8226; " + String(w).replace(/</g, "&lt;") + "</div>")
        .join("");
      warningsBox.classList.remove("hidden");
    } else {
      warningsBox.classList.add("hidden");
    }

    renderItemList();
  }

  function renderItemList() {
    const container = document.getElementById("psa-results-list");
    const emptyLabel = document.getElementById("psa-results-empty");
    const term = state.searchTerm.trim().toLowerCase();

    const filtered = state.items.filter((item) => {
      if (state.activeFilter !== "ALL" && item.status !== state.activeFilter) return false;
      if (!term) return true;
      return (
        (item.question_text || "").toLowerCase().includes(term) ||
        (item.question_id || "").toLowerCase().includes(term) ||
        (item.message || "").toLowerCase().includes(term)
      );
    });

    if (filtered.length === 0) {
      container.innerHTML = "";
      emptyLabel.classList.remove("hidden");
      return;
    }
    emptyLabel.classList.add("hidden");

    container.innerHTML = filtered.map(renderItemCard).join("");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderItemCard(item) {
    const meta = STATUS_META[item.status] || STATUS_META.MANUAL;
    

    let extractedRows = "";
    if (item.extracted_data && item.extracted_data.length > 0) {
      extractedRows = item.extracted_data
        .map(
          (row) =>
            "<tr><td class=\"py-1 pe-4 text-slate-500\">" +
            escapeHtml(row["متغیر"]) +
            "</td><td class=\"py-1 font-medium text-slate-700\">" +
            escapeHtml(row["مقدار استخراج‌شده"]) +
            "</td></tr>"
        )
        .join("");
    }

    let conditionRows = "";
    if (item.condition_breakdown && item.condition_breakdown.length > 0) {
      conditionRows = item.condition_breakdown
        .map(
          (cond) =>
            "<li class=\"flex items-start gap-1.5\"><span class=\"" +
            (cond.result === "PASSED" ? "text-emerald-600" : "text-red-500") +
            "\">" + (cond.result === "PASSED" ? "✓" : "✕") + "</span><span>" +
            escapeHtml(cond.condition) +
            "</span></li>"
        )
        .join("");
    }

    return (
      '<details class="psa-item rounded-xl border p-4 ' + meta.classes + '"' +
      '>' +
      '<summary class="flex items-center justify-between gap-3">' +
      '<div class="flex items-center gap-2 min-w-0">' +
      '<span class="psa-chevron shrink-0 text-slate-400">' +
      '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>' +
      "</span>" +
      '<span class="truncate text-sm font-medium text-slate-800">' + escapeHtml(item.question_text) + "</span>" +
      "</div>" +
      '<span class="shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold ' + meta.badge + '">' + meta.label + "</span>" +
      "</summary>" +
      '<div class="mt-3 space-y-3 text-sm text-slate-600">' +
      (item.message ? '<p>' + escapeHtml(item.message) + "</p>" : "") +
      (conditionRows ? '<ul class="space-y-1 text-xs">' + conditionRows + "</ul>" : "") +
      (extractedRows
        ? '<table class="w-full text-xs"><tbody>' + extractedRows + "</tbody></table>"
        : "") +
      "</div>" +
      "</details>"
    );
  }

  function bindResultFilters() {
    document.getElementById("psa-status-filters").addEventListener("click", function (event) {
      const btn = event.target.closest(".psa-filter-btn");
      if (!btn) return;
      state.activeFilter = btn.dataset.filter;
      document.querySelectorAll(".psa-filter-btn").forEach((el) => {
        el.classList.toggle("bg-brand-600", el === btn);
        el.classList.toggle("text-white", el === btn);
        el.classList.toggle("bg-slate-100", el !== btn);
        el.classList.toggle("text-slate-600", el !== btn);
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
    showStep(0);
  });
})();
