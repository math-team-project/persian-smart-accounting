/**
 * منطق صفحهٔ پروژه و صفحهٔ پروژه‌ها (داشبورد).
 *
 * دو کار انجام می‌دهد:
 *   ۱) پنل «کارهای در جریان» صفحهٔ پروژه را از ``GET /api/projects/<id>/jobs``
 *      به‌روز نگه می‌دارد (هر اجرا مستقل). این پنل *فقط نمایش* است -- وضعیت واقعی
 *      job ها سمت سرور است، بنابراین رفتن به کارگاه دیگر یا بستن مرورگر پیشرفت
 *      را از بین نمی‌برد. پس از پایان هر اجرا، فهرست تاریخچه هم تازه می‌شود.
 *   ۲) گفت‌وگوی تأیید حذف پروژه را در داشبورد باز/بسته می‌کند.
 *
 * سبک کدنویسی مطابق checklist.js/audit_summary.js: جاوااسکریپت خالص، شکست‌های
 * واضح و پیام فارسی با ``window.psaShowToast``.
 */
(function () {
  "use strict";

  var POLL_INTERVAL_MS = 2500;

  /* آیکون‌های درون‌خطی (هم‌سبک با api/utils/icons.py) */
  var ICONS = {
    spinner: '<span class="psa-spinner" aria-hidden="true"></span>',
    check: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    alert: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    download: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
    clock: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>'
  };

  var STATUS_META = {
    pending: { label: "در صف اجرا", badge: "psa-badge psa-badge-neutral" },
    running: { label: "در حال اجرا", badge: "psa-badge psa-badge-info" },
    done: { label: "تکمیل‌شده", badge: "psa-badge psa-badge-success" },
    error: { label: "ناموفق", badge: "psa-badge psa-badge-danger" }
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /* ------------------------------------------------------------------
     پنل کارهای در جریان
     ------------------------------------------------------------------ */
  function initJobsPanel() {
    var root = document.getElementById("psa-project-root");
    var panel = document.getElementById("psa-jobs-panel");
    if (!root || !panel) return;

    var jobsUrl = root.dataset.jobsUrl;
    var historyUrl = root.dataset.historyUrl;
    var countBadge = document.getElementById("psa-jobs-count");
    var pollTimer = null;
    var lastActiveCount = null;

    function renderJob(job) {
      var meta = STATUS_META[job.status] || STATUS_META.pending;
      var isActive = job.status === "running" || job.status === "pending";
      var progress = Math.max(0, Math.min(100, job.progress || 0));

      var statusBadge = isActive
        ? '<span class="' + meta.badge + '">' + ICONS.spinner + meta.label + "</span>"
        : '<span class="' + meta.badge + '">' + meta.label + "</span>";

      var progressBlock = isActive
        ? '<div class="mt-3">' +
            '<div class="flex items-center justify-between">' +
              '<span class="psa-section-hint">' + escapeHtml(job.stage || "در حال پردازش…") + "</span>" +
              '<span class="psa-num text-xs text-slate-500">' + progress.toLocaleString("fa-IR") + "٪</span>" +
            "</div>" +
            '<div class="psa-meter mt-1.5" role="progressbar" aria-valuemin="0" aria-valuemax="100" ' +
              'aria-valuenow="' + progress + '"><span style="width:' + progress + '%"></span></div>' +
          "</div>"
        : "";

      // پیام خطای سرویس‌های بیرونی می‌تواند بلند باشد؛ متن کامل در title می‌ماند و
      // در کارت فقط بخش آغازین آن نشان داده می‌شود.
      var errorText = job.error ? String(job.error) : "";
      var errorBlock = errorText
        ? '<p class="psa-note is-error mt-3" title="' + escapeHtml(errorText) + '">' + ICONS.alert +
          "<span>" + escapeHtml(errorText.slice(0, 240)) + (errorText.length > 240 ? "…" : "") + "</span></p>"
        : "";

      var actionBlock =
        job.download_ready && job.download_url
          ? '<a href="' + escapeHtml(job.download_url) + '" class="psa-btn psa-btn-gold psa-btn-sm">' +
              ICONS.download + " دانلود نتیجه</a>"
          : "";

      return (
        '<article class="psa-card p-4">' +
          '<div class="flex flex-wrap items-center justify-between gap-2">' +
            "<div>" +
              '<p class="text-sm font-bold text-slate-900">' + escapeHtml(job.workshop_name_fa) + "</p>" +
              '<p class="psa-meta mt-1">' + ICONS.clock + " شمارهٔ اجرا: " +
                '<span dir="ltr" class="psa-num">#' + job.run_id + "</span></p>" +
            "</div>" +
            '<div class="flex items-center gap-2">' + statusBadge + actionBlock + "</div>" +
          "</div>" +
          progressBlock +
          errorBlock +
        "</article>"
      );
    }

    function render(jobs) {
      if (!jobs.length) {
        panel.innerHTML =
          '<p class="psa-empty">' + ICONS.check +
          "<span>در حال حاضر اجرای در جریانی وجود ندارد. برای شروع، یکی از کارگاه‌های پایین را باز کنید.</span></p>";
        return;
      }
      panel.innerHTML = jobs.map(renderJob).join("");
    }

    async function refreshHistory() {
      if (!historyUrl) return;
      var historyPanel = document.getElementById("psa-history-panel");
      if (!historyPanel) return;
      try {
        var response = await fetch(historyUrl, { headers: { "X-Requested-With": "fetch" } });
        if (!response.ok) return;
        historyPanel.innerHTML = await response.text();
      } catch (err) {
        // تازه‌سازی تاریخچه اختیاری است؛ خطای آن نباید تجربهٔ کاربر را قطع کند.
      }
    }

    async function pollOnce() {
      try {
        var response = await fetch(jobsUrl, { headers: { "X-Requested-With": "fetch" } });
        if (!response.ok) {
          throw new Error("پاسخ سرور: " + response.status);
        }
        var data = await response.json();
        var jobs = data.jobs || [];
        render(jobs);

        var activeCount = jobs.filter(function (job) {
          return job.status === "running" || job.status === "pending";
        }).length;

        if (countBadge) {
          countBadge.textContent = jobs.length
            ? activeCount.toLocaleString("fa-IR") + " در جریان از " + jobs.length.toLocaleString("fa-IR") + " اجرای اخیر"
            : "بدون اجرا";
        }

        // وقتی شمار اجراهای فعال کم شود یعنی کاری تمام شده -- فهرست تاریخچه
        // (و در نتیجه لینک دانلود نتیجهٔ جدید) تازه می‌شود.
        if (lastActiveCount !== null && activeCount < lastActiveCount) {
          refreshHistory();
        }
        lastActiveCount = activeCount;
      } catch (err) {
        stopPolling();
        if (typeof window.psaShowToast === "function") {
          window.psaShowToast(
            "دریافت وضعیت اجراها ناموفق بود: " + (err && err.message ? err.message : String(err)) +
              " — برای ادامهٔ پیگیری، صفحه را دوباره بارگذاری کنید.",
            "error"
          );
        }
      }
    }

    function startPolling() {
      stopPolling();
      pollOnce();
      pollTimer = window.setInterval(pollOnce, POLL_INTERVAL_MS);
    }

    function stopPolling() {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    // فقط زمانی پیمایش می‌کنیم که کاربر در همین صفحه است (صرفه‌جویی در درخواست‌ها)
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        stopPolling();
      } else {
        startPolling();
      }
    });

    var refreshBtn = document.getElementById("psa-history-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", refreshHistory);

    startPolling();
  }

  /* ------------------------------------------------------------------
     گفت‌وگوی تأیید حذف پروژه (داشبورد)
     ------------------------------------------------------------------ */
  function initDeleteDialog() {
    var dialog = document.getElementById("psa-delete-dialog");
    var form = document.getElementById("psa-delete-form");
    var nameLabel = document.getElementById("psa-delete-project-name");
    if (!dialog || !form) return;

    function open(projectId, projectName) {
      form.action = "/projects/" + encodeURIComponent(projectId) + "/delete";
      if (nameLabel) nameLabel.textContent = projectName;
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else if (window.confirm("پروژهٔ «" + projectName + "» و همهٔ داده‌های آن برای همیشه حذف شود؟")) {
        form.submit();
      }
    }

    document.querySelectorAll("[data-psa-delete-project]").forEach(function (button) {
      button.addEventListener("click", function () {
        open(button.dataset.psaDeleteProject, button.dataset.psaProjectName || "");
      });
    });

    var cancelBtn = document.getElementById("psa-delete-cancel");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", function () {
        if (typeof dialog.close === "function") dialog.close();
      });
    }

    // بستن با کلید Escape به‌صورت پیش‌فرض توسط خود <dialog> انجام می‌شود.
  }

  document.addEventListener("DOMContentLoaded", function () {
    initJobsPanel();
    initDeleteDialog();
  });
})();
