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

  /* تنظیمات این کارگاه که از قالب صفحه تزریق می‌شود: پیشوند API همین کارگاه
     داخل پروژهٔ جاری (``/api/projects/<id>/audit-summary``) و کلید نگه‌داری
     شناسهٔ job. مسیر API از رجیستری کارگاه‌ها می‌آید، بنابراین هیچ آدرسی این‌جا
     هاردکد نیست. */
  const WORKSHOP_CONFIG = window.PSA_WORKSHOP || {};
  const API_BASE = WORKSHOP_CONFIG.apiBase || "/api/summary";

  /* کلید نگه‌داری شناسه‌ی آخرین job در مرورگر (به‌ازای هر پروژه جداگانه) -- با
     این کار جابه‌جایی بین کارگاه‌ها یا بارگذاری دوبارهٔ صفحه، پردازش در حال
     اجرا/تمام‌شده را از بین نمی‌برد (job ها در حافظه‌ی سرور باقی می‌مانند؛
     نگاه کنید ``api/jobs/job_manager.py``). */
  const STORAGE_KEY = WORKSHOP_CONFIG.storageKey || "psa.summary.jobId";

  const state = {
    jobId: null,
    runId: null,
    pollTimer: null,
    step: null,
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
      // در حالت ناشناس/مسدود، فقط خاصیت «ماندگاری» از دست می‌رود.
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

  /** بازگشت به مرحله‌ی آپلود برای شروع یک خلاصه‌سازی تازه. */
  function resetWorkspace() {
    stopPolling();
    state.jobId = null;
    state.runId = null;
    state.step = null;
    clearStoredJobId();

    const preview = document.getElementById("psa-summary-preview");
    if (preview) preview.innerHTML = "";
    const source = document.getElementById("psa-result-source");
    if (source) source.textContent = "";
    document.getElementById("psa-results-warnings").classList.add("hidden");

    if (typeof window.psaSetProgress === "function") {
      window.psaSetProgress(0, "در حال آماده‌سازی...", []);
    }
    showStep(0);
    window.psaHideToast();
  }

  /**
   * بازیابی پیگیری/نتیجه‌ی پردازشی که قبلاً شروع شده است. اگر job دیگر روی
   * سرور موجود نباشد (ری‌استارت سرور یا انقضا)، بی‌سروصدا به مرحله‌ی آپلود
   * برمی‌گردیم.
   */
  async function restoreJob() {
    try {
      const response = await fetch(API_BASE + "/jobs/" + encodeURIComponent(state.jobId));
      if (!response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      const job = await response.json();
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
      const response = await fetch(API_BASE + "/jobs", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      const data = await response.json();
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
      const response = await fetch(API_BASE + "/jobs/" + encodeURIComponent(state.jobId));
      if (!response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      const job = await response.json();
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
        "دریافت وضعیت پردازش ناموفق بود: " + (err && err.message ? err.message : String(err)) +
          " — لطفاً صفحه را دوباره بارگذاری کنید.",
        "error"
      );
    }
  }

  async function loadResults() {
    try {
      const response = await fetch(API_BASE + "/jobs/" + encodeURIComponent(state.jobId) + "/results");
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
    /* متادیتای پردازش: نام فایل مبدأ و زمان صرف‌شده */
    var metaParts = [];
    if (data.source_filename) metaParts.push("برگرفته از: " + data.source_filename);
    if (typeof data.elapsed_seconds === "number" && data.elapsed_seconds > 0) {
      metaParts.push(
        "زمان پردازش: " + Math.round(data.elapsed_seconds).toLocaleString("fa-IR") + " ثانیه"
      );
    }
    document.getElementById("psa-result-source").textContent = metaParts.join("  •  ");

    // دانلود از مسیر «اجرا» (نه job) تا فایل ذخیره‌شده در تاریخچه هم قابل دریافت باشد.
    const downloadLink = document.getElementById("psa-download-summary");
    if (state.runId) {
      downloadLink.href = API_BASE + "/runs/" + encodeURIComponent(state.runId) + "/download";
    }

    const previewBox = document.getElementById("psa-summary-preview");
    previewBox.innerHTML =
      data.summary_html ||
      '<p class="text-sm text-slate-400">پیش‌نمایشی در دسترس نیست.</p>';

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

    // «شروع خلاصه‌سازی جدید» -- از مرحله‌ی پردازش یا نتیجه به آپلود برمی‌گردد.
    var restartBtn = document.getElementById("psa-restart-btn");
    if (restartBtn) restartBtn.addEventListener("click", resetWorkspace);
    var restartBtnResults = document.getElementById("psa-restart-btn-results");
    if (restartBtnResults) restartBtnResults.addEventListener("click", resetWorkspace);

    // بازیابی پردازش قبلی (در حال اجرا یا تمام‌شده) در صورت وجود
    var storedJobId = readStoredJobId();
    if (storedJobId) {
      state.jobId = storedJobId;
      restoreJob();
    } else {
      showStep(0);
    }
  });
})();
