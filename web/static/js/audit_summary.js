/**
 * منطق صفحه‌ی کارگاه خلاصه‌سازی گزارش حسابرسی (آپلود -> پردازش -> نتیجه).
 *
 * جاوااسکریپت خالص (بدون فریم‌ورک)، دقیقاً مطابق الگوی checklist.js: شکست‌های
 * واضح و پیام خطای فارسی به کاربر (هیچ خطایی نباید بی‌صدا نادیده گرفته شود)
 * با استفاده از ``window.psaShowToast``.
 */
(function () {
  "use strict";

  const POLL_INTERVAL_MS = 1500;

  const state = {
    jobId: null,
    pollTimer: null,
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
      // پاسخ JSON نبود؛ پیام پیش‌فرض زیر برگردانده می‌شود.
    }
    return "خطای غیرمنتظره‌ای از سرور دریافت شد (کد " + response.status + ").";
  }

  async function submitForm(event) {
    event.preventDefault();
    const form = document.getElementById("psa-summary-form");
    const submitBtn = document.getElementById("psa-submit-btn");
    const formData = new FormData(form);

    submitBtn.disabled = true;
    try {
      const response = await fetch("/api/summary/jobs", {
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
      const response = await fetch("/api/summary/jobs/" + encodeURIComponent(state.jobId));
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
        window.psaShowToast(job.error || "پردازش با خطا متوقف شد.", "error");
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
      const response = await fetch("/api/summary/jobs/" + encodeURIComponent(state.jobId) + "/results");
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
    document.getElementById("psa-result-source").textContent = data.source_filename
      ? "برگرفته از: " + data.source_filename
      : "";

    const downloadLink = document.getElementById("psa-download-summary");
    downloadLink.href = "/api/summary/jobs/" + encodeURIComponent(state.jobId) + "/download";

    const previewBox = document.getElementById("psa-summary-preview");
    previewBox.innerHTML = data.summary_html || '<p class="text-sm text-slate-400">پیش‌نمایشی در دسترس نیست.</p>';

    const warningsBox = document.getElementById("psa-results-warnings");
    if (data.warnings && data.warnings.length > 0) {
      warningsBox.innerHTML = data.warnings
        .map((w) => "<div>&#8226; " + String(w).replace(/</g, "&lt;") + "</div>")
        .join("");
      warningsBox.classList.remove("hidden");
    } else {
      warningsBox.classList.add("hidden");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("psa-summary-form");
    if (!form) return;
    form.addEventListener("submit", submitForm);
    showStep(0);
  });
})();
