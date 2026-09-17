/**
 * منطق بخش «تنظیمات هوش مصنوعی» صفحهٔ پروژه.
 *
 * یک فرم برای هر کارگاه (هرکدام داخل یک <details> با ``data-psa-ai-panel``).
 * هر فرم آدرس خودش را از ``data-settings-url`` می‌خواند (سمت سرور ساخته می‌شود)
 * بنابراین هیچ آدرسی در این فایل هاردکد نیست.
 *
 * قواعد رفتاری:
 *   * خالی گذاشتن هر فیلد یعنی «از پیش‌فرض سامانه استفاده کن» -- همان چیزی که
 *     به کاربر هم گفته شده است.
 *   * فیلد کلید API هرگز با مقدار ذخیره‌شده پر نمی‌شود؛ خالی‌بودنِ آن یعنی
 *     «کلید قبلی را نگه دار» (مگر چک‌باکس حذف تیک بخورد).
 *   * اعلان‌ها با ``window.psaShowToast`` (کامپوننت مشترک) نمایش داده می‌شوند.
 *
 * سبک کدنویسی مطابق project.js / checklist.js: جاوااسکریپت خالص، بدون وابستگی.
 */
(function () {
  "use strict";

  function panelResult(panel, message, level) {
    var box = panel.querySelector("[data-psa-result]");
    if (!box) return;
    box.textContent = message || "";
    box.classList.remove("is-success", "is-error");
    if (level) box.classList.add(level === "success" ? "is-success" : "is-error");
  }

  function setBusy(panel, busy) {
    var spinner = panel.querySelector("[data-psa-busy]");
    if (spinner) spinner.classList.toggle("hidden", !busy);
    panel.querySelectorAll("button").forEach(function (button) {
      button.disabled = busy;
    });
  }

  function toast(message, level) {
    if (typeof window.psaShowToast === "function") window.psaShowToast(message, level);
  }

  function field(panel, name) {
    return panel.querySelector('[data-psa-field="' + name + '"]');
  }

  /** بدنهٔ درخواست از روی مقادیر فرم -- فقط چیزهایی که کاربر وارد کرده است. */
  function collect(panel) {
    var payload = {};
    var keyInput = field(panel, "api_key");
    var urlInput = field(panel, "api_url");
    var modelInput = field(panel, "model");
    var tempInput = field(panel, "temperature");
    var tokensInput = field(panel, "max_output_tokens");
    var clearInput = field(panel, "clear_api_key");

    if (keyInput && keyInput.value.trim()) payload.api_key = keyInput.value.trim();
    if (clearInput && clearInput.checked) payload.clear_api_key = true;
    // فیلدهای غیرراز: رشتهٔ خالی به سرور می‌رود تا «پاک شود / پیش‌فرض بگیرد».
    if (urlInput) payload.api_url = urlInput.value.trim();
    if (modelInput) payload.model = modelInput.value.trim();
    if (tempInput && tempInput.value.trim() !== "") payload.temperature = Number(tempInput.value);
    if (tokensInput && tokensInput.value.trim() !== "") {
      payload.max_output_tokens = parseInt(tokensInput.value, 10);
    }
    return payload;
  }

  async function request(url, method, payload) {
    var response = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify(payload)
    });
    var data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    if (!response.ok) {
      var detail = data && data.detail ? data.detail : "پاسخ نامعتبر از سرور (" + response.status + ")";
      throw new Error(detail);
    }
    return data || {};
  }

  /** آیا این کارگاه در این پروژه مقدار اختصاصی (غیرپیش‌فرض) ذخیره‌شده دارد؟ */
  function hasStoredOverrides(result) {
    if (!result) return false;
    return !!(
      result.api_url ||
      result.model ||
      result.temperature != null ||
      result.max_output_tokens != null
    );
  }

  function applySavedState(panel, result) {
    var badge = panel.querySelector("[data-psa-key-badge]");
    var summary = panel.querySelector("[data-psa-ai-summary]");
    var keyInput = field(panel, "api_key");
    var keySet = !!(result && result.api_key_set);

    if (badge) {
      badge.textContent = keySet ? "کلید اختصاصی" : "کلید پیش‌فرض";
      badge.classList.toggle("psa-badge-success", keySet);
      badge.classList.toggle("psa-badge-neutral", !keySet);
    }
    if (summary) {
      var hasCustom = keySet || hasStoredOverrides(result);
      summary.textContent = hasCustom
        ? "تنظیمات اختصاصی این پروژه فعال است."
        : "در حال استفاده از پیش‌فرض‌های سامانه.";
    }
    if (keyInput) {
      // کلید هرگز به فرم برنمی‌گردد؛ فقط مقدار واردشده پاک می‌شود.
      keyInput.value = "";
      keyInput.placeholder = keySet
        ? "•••• تنظیم شده — برای تغییر، مقدار جدید وارد کنید"
        : "خالی بگذارید تا از کلید پیش‌فرض سامانه استفاده شود";
    }
    var clearInput = field(panel, "clear_api_key");
    if (clearInput) clearInput.checked = false;

    // چک‌باکس «حذف کلید ذخیره‌شده» فقط وقتی معنادار است که کلیدی ذخیره شده باشد.
    var clearWrap = panel.querySelector("[data-psa-clear-wrap]");
    if (clearWrap) clearWrap.classList.toggle("hidden", !keySet);
  }

  function initPanel(panel) {
    var url = panel.dataset.settingsUrl;
    if (!url) return;
    var form = panel.querySelector("[data-psa-ai-form]");
    if (!form) return;

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      setBusy(panel, true);
      panelResult(panel, "در حال ذخیره…", null);
      try {
        var saved = await request(url, "PUT", collect(panel));
        applySavedState(panel, saved);
        panelResult(panel, "تنظیمات ذخیره شد.", "success");
        toast("تنظیمات هوش مصنوعی «" + (saved.display_name_fa || "") + "» ذخیره شد.", "success");
      } catch (err) {
        var message = err && err.message ? err.message : String(err);
        panelResult(panel, "ذخیره نشد: " + message, "error");
        toast("ذخیرهٔ تنظیمات ناموفق بود: " + message, "error");
      } finally {
        setBusy(panel, false);
      }
    });

    var testButton = panel.querySelector("[data-psa-test]");
    if (testButton) {
      testButton.addEventListener("click", async function () {
        setBusy(panel, true);
        panelResult(panel, "در حال آزمایش اتصال…", null);
        try {
          var result = await request(url + "/test", "POST", collect(panel));
          var ok = !!result.ok;
          panelResult(panel, result.message || (ok ? "اتصال برقرار است." : "اتصال برقرار نشد."),
            ok ? "success" : "error");
          toast(result.message || (ok ? "اتصال برقرار است." : "اتصال برقرار نشد."),
            ok ? "success" : "error");
        } catch (err) {
          var message = err && err.message ? err.message : String(err);
          panelResult(panel, "تست اتصال ناموفق بود: " + message, "error");
          toast("تست اتصال ناموفق بود: " + message, "error");
        } finally {
          setBusy(panel, false);
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-psa-ai-panel]").forEach(initPanel);
  });
})();
