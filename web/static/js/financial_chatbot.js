/**
 * منطق صفحه‌ی کارگاه «چت‌بات مالی» (گفتگوهای نام‌دار + پرسش/پاسخ زنده).
 *
 * ساختار کلی از همان الگوی ``budget_analysis.js``/``checklist.js`` پیروی می‌کند
 * (بدون هیچ وابستگی بیرونی و بدون مرحله‌ی build، آدرس‌ها از ``window.PSA_WORKSHOP``
 * که رجیستری تزریق کرده می‌آیند، و همه‌ی خطاها با ``window.psaShowToast`` نمایش
 * داده می‌شوند). تفاوت اصلی این است که این‌جا حلقه‌ی polling وجود ندارد: پاسخ
 * یک درخواست/پاسخ زنده است.
 *
 * دو قرارداد مهم این فایل که باید حفظ شوند:
 *
 * ۱) **زمینه‌ی چندنوبتی فقط در حافظه است.** آرایه‌ی ``state.history`` تنها
 *    نگه‌دارنده‌ی پیام‌های گفتگوی جاری است؛ در ``localStorage`` نوشته *نمی‌شود*
 *    (تنها شناسه‌ی آخرین گفتگوی باز ذخیره می‌شود، که صرفاً متادیتا است) و با
 *    هر بارگذاری مجدد صفحه یا انتخاب یک گفتگوی دیگر خالی می‌شود. به همین دلیل
 *    باز کردن دوباره‌ی یک گفتگوی قدیمی، گفتگویی خالی نشان می‌دهد -- این عمدی است.
 *
 * ۲) **خروجی مدل هرگز به‌صورت HTML خام تزریق نمی‌شود.** مسیر امن این است:
 *    ابتدا کل متن با ``escapeHtml`` بی‌اثر می‌شود، سپس تبدیل Markdown انجام
 *    می‌گیرد و در پایان، فقط تگ‌هایی که خودِ ``renderMarkdown`` ساخته است داخل
 *    عنصر پاسخ قرار می‌گیرد. یعنی یک ``<script>`` در پاسخ مدل به متن قابل‌مشاهده
 *    تبدیل می‌شود، نه به کد اجراشدنی. لینک‌ها هم فقط با پیشوندهای مجاز
 *    (https/http/mailto/نسبی) رندر می‌شوند تا ``javascript:`` بی‌اثر بماند.
 */
(function () {
  "use strict";

  var CFG = window.PSA_WORKSHOP || {};
  var API_BASE = CFG.apiBase || "";
  var STORAGE_KEY = CFG.storageKey || "psa.financial_chatbot.sessionId";

  /* تعداد نوبت‌هایی که در حافظه نگه داشته می‌شود (بیشتر از آنچه فرستاده می‌شود،
     تا پیام‌های نمایش‌داده‌شده‌ی گفتگو ناقص نشوند). */
  var CLIENT_HISTORY_LIMIT = 12;
  /* همان سقفی که سرور هم اعمال می‌کند (``MAX_CONTEXT_TURNS`` در سرویس). */
  var HISTORY_TURNS_SENT = Number(CFG.maxHistoryTurns || 6);

  var CONFIDENCE_FA = {
    high: { label: "اطمینان بالا", cls: "psa-badge-success" },
    medium: { label: "اطمینان متوسط", cls: "psa-badge-warning" },
    needs_review: { label: "نیازمند بازبینی", cls: "psa-badge-danger" }
  };

  var ICONS = {
    user: '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    bot: '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v2"/><path d="m5 8 1.5 1.5"/><path d="M19 8l-1.5 1.5"/><rect x="4" y="9" width="16" height="10" rx="3"/><circle cx="9" cy="14" r="1"/><circle cx="15" cy="14" r="1"/></svg>',
    chat: '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/></svg>',
    file: '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><polyline points="14 2 14 8 20 8"/></svg>'
  };

  var state = {
    sessionId: null,
    sessions: [],
    /** زمینه‌ی گفتگوی *همین صفحه* -- هرگز ذخیره نمی‌شود. */
    history: [],
    busy: false,
    controller: null
  };

  /* ==================================================================
     کمک‌تابع‌های عمومی
     ================================================================== */
  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function setHidden(el, hidden) {
    if (el) el.classList.toggle("hidden", !!hidden);
  }

  /** ارقام لاتین ورودی مدل را فارسی نشان می‌دهد (فقط برای نمایش تاریخ/شماره). */
  function faDigits(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[0-9]/g, function (d) {
      return "۰۱۲۳۴۵۶۷۸۹".charAt(Number(d));
    });
  }

  function formatDate(value) {
    if (!value) return "";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "";
    try {
      return faDigits(date.toLocaleDateString("fa-IR"));
    } catch (err) {
      return "";
    }
  }

  async function parseErrorDetail(response) {
    try {
      var data = await response.json();
      if (data && data.detail) {
        return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch (err) {
      /* پاسخ JSON نبود. */
    }
    return "خطای غیرمنتظره‌ای از سرور دریافت شد (کد " + response.status + ").";
  }

  function readStoredSessionId() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      var parsed = raw === null ? NaN : Number(raw);
      return isFinite(parsed) && parsed > 0 ? parsed : null;
    } catch (err) {
      return null;
    }
  }

  function storeSessionId(sessionId) {
    try {
      if (sessionId === null || sessionId === undefined) {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, String(sessionId));
      }
    } catch (err) {
      /* حالت ناشناس/مسدود: فقط یادآوری گفتگوی باز از دست می‌رود. */
    }
  }

  /* ==================================================================
     رندر امن Markdown (فرار دادن اول، تبدیل بعد)
     ================================================================== */

  /** فقط پیشوندهای بی‌خطر برای ``href`` پذیرفته می‌شوند. */
  function safeUrl(raw) {
    var text = String(raw || "").trim();
    var decoded = text.replace(/&amp;/g, "&");
    if (/^https?:\/\//i.test(decoded)) return text;
    if (/^mailto:/i.test(decoded)) return text;
    if (decoded.charAt(0) === "/" || decoded.charAt(0) === "#") return text;
    return "";
  }

  /**
   * قالب‌بندی درون‌خطی. ورودی **از قبل فرار داده شده** است، بنابراین هر
   * ``<``/``>`` در متن مدل الآن یک entity است و تنها تگ‌های خروجیِ همین تابع
   * واقعاً HTML می‌شوند.
   */
  function renderInline(escaped) {
    var codes = [];
    var text = escaped.replace(/`([^`]+)`/g, function (match, code) {
      codes.push(code);
      return "\u0000" + (codes.length - 1) + "\u0000";
    });

    text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (match, label, href) {
      var url = safeUrl(href);
      if (!url) return label; // پروتکل ناشناخته: متن ساده، بدون پیوند
      return (
        '<a class="psa-chat-link" href="' + url + '" target="_blank" rel="noopener noreferrer">' +
        label + "</a>"
      );
    });

    text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    text = text.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");

    return text.replace(/\u0000(\d+)\u0000/g, function (match, index) {
      return "<code>" + codes[Number(index)] + "</code>";
    });
  }

  function splitTableRow(line) {
    var text = String(line).trim();
    if (text.charAt(0) === "|") text = text.slice(1);
    if (text.charAt(text.length - 1) === "|") text = text.slice(0, -1);
    return text.split("|").map(function (cell) {
      return cell.trim();
    });
  }

  function isTableSeparator(line) {
    var cells = splitTableRow(line);
    if (!cells.length) return false;
    return cells.every(function (cell) {
      return /^:?-{2,}:?$/.test(cell);
    });
  }

  function isUnorderedItem(line) {
    return /^\s*[-*+]\s+\S/.test(line);
  }

  function isOrderedItem(line) {
    return /^\s*\d+[.)]\s+\S/.test(line);
  }

  function isBlockStart(line) {
    var text = line.trim();
    if (!text) return true;
    if (/^(#{1,6})\s+/.test(line)) return true;
    if (/^```/.test(text) || /^~~~/.test(text)) return true;
    if (/^>\s?/.test(line)) return true;
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(text)) return true;
    if (isUnorderedItem(line) || isOrderedItem(line)) return true;
    if (text.charAt(0) === "|") return true;
    return false;
  }

  /**
   * تبدیل Markdown به HTML **بی‌خطر**: ابتدا کل متن فرار داده می‌شود و بعد
   * ساختار ساخته می‌شود، بنابراین خروجی هرگز نمی‌تواند HTML مدل را اجرا کند.
   */
  function renderMarkdown(source) {
    var lines = String(source === null || source === undefined ? "" : source)
      .replace(/\r\n?/g, "\n")
      .split("\n");
    var html = [];
    var paragraph = [];
    var index = 0;

    function flushParagraph() {
      if (!paragraph.length) return;
      html.push("<p>" + renderInline(escapeHtml(paragraph.join(" "))) + "</p>");
      paragraph = [];
    }

    while (index < lines.length) {
      var line = lines[index];
      var trimmed = line.trim();

      if (!trimmed) {
        flushParagraph();
        index += 1;
        continue;
      }

      // بلوک کد حصاردار -- محتوایش دست‌نخورده (فقط فرار‌داده‌شده) می‌ماند.
      if (/^(```|~~~)/.test(trimmed)) {
        flushParagraph();
        var fence = trimmed.slice(0, 3);
        var code = [];
        index += 1;
        while (index < lines.length && lines[index].trim().indexOf(fence) !== 0) {
          code.push(lines[index]);
          index += 1;
        }
        index += 1; // خط پایانی حصار
        html.push('<pre class="psa-chat-code"><code>' + escapeHtml(code.join("\n")) + "</code></pre>");
        continue;
      }

      // تیتر
      var heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        flushParagraph();
        var level = heading[1].length;
        html.push("<h" + level + ">" + renderInline(escapeHtml(heading[2].trim())) + "</h" + level + ">");
        index += 1;
        continue;
      }

      // خط جداکننده
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        flushParagraph();
        html.push("<hr />");
        index += 1;
        continue;
      }

      // نقل‌قول
      if (/^>\s?/.test(line)) {
        flushParagraph();
        var quoted = [];
        while (index < lines.length && /^>\s?/.test(lines[index])) {
          quoted.push(lines[index].replace(/^>\s?/, ""));
          index += 1;
        }
        html.push(
          "<blockquote>" + renderInline(escapeHtml(quoted.join(" "))) + "</blockquote>"
        );
        continue;
      }

      // جدول: سرصفحه + خط جداکننده
      if (trimmed.charAt(0) === "|" && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
        flushParagraph();
        var headers = splitTableRow(lines[index]);
        var rows = [];
        index += 2;
        while (index < lines.length && lines[index].trim().charAt(0) === "|") {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        var thead = headers
          .map(function (cell) {
            return "<th>" + renderInline(escapeHtml(cell)) + "</th>";
          })
          .join("");
        var tbody = rows
          .map(function (row) {
            var cells = [];
            for (var column = 0; column < headers.length; column += 1) {
              cells.push("<td>" + renderInline(escapeHtml(row[column] || "")) + "</td>");
            }
            return "<tr>" + cells.join("") + "</tr>";
          })
          .join("");
        html.push(
          '<div class="psa-table-wrap"><table class="psa-table psa-chat-table"><thead><tr>' +
            thead + "</tr></thead><tbody>" + tbody + "</tbody></table></div>"
        );
        continue;
      }

      // فهرست نقطه‌ای
      if (isUnorderedItem(line)) {
        flushParagraph();
        var bullets = [];
        while (index < lines.length && isUnorderedItem(lines[index])) {
          bullets.push(lines[index].replace(/^\s*[-*+]\s+/, ""));
          index += 1;
        }
        html.push(
          "<ul>" + bullets
            .map(function (item) {
              return "<li>" + renderInline(escapeHtml(item)) + "</li>";
            })
            .join("") + "</ul>"
        );
        continue;
      }

      // فهرست شماره‌دار
      if (isOrderedItem(line)) {
        flushParagraph();
        var numbered = [];
        while (index < lines.length && isOrderedItem(lines[index])) {
          numbered.push(lines[index].replace(/^\s*\d+[.)]\s+/, ""));
          index += 1;
        }
        html.push(
          "<ol>" + numbered
            .map(function (item) {
              return "<li>" + renderInline(escapeHtml(item)) + "</li>";
            })
            .join("") + "</ol>"
        );
        continue;
      }

      paragraph.push(trimmed);
      index += 1;

      // اگر خط بعدی شروع یک بلوک باشد، پاراگراف همین‌جا بسته می‌شود.
      if (index < lines.length && isBlockStart(lines[index])) flushParagraph();
    }

    flushParagraph();
    return html.join("");
  }

  /* ==================================================================
     رندر گفتگو
     ================================================================== */
  function clearMessages() {
    var box = byId("psa-chat-messages");
    if (box) box.innerHTML = "";
  }

  function hidePlaceholder() {
    var placeholder = byId("psa-chat-placeholder");
    if (placeholder) placeholder.remove();
  }

  function appendRow(role, innerHtml, extraClass) {
    var box = byId("psa-chat-messages");
    if (!box) return null;
    hidePlaceholder();
    var row = document.createElement("div");
    row.className = "psa-chat-row is-" + role + (extraClass ? " " + extraClass : "");
    row.innerHTML =
      '<span class="psa-chat-avatar is-' + role + '" aria-hidden="true">' +
      (role === "user" ? ICONS.user : ICONS.bot) +
      "</span>" +
      '<div class="psa-chat-bubble">' + innerHtml + "</div>";
    box.appendChild(row);
    scrollToBottom();
    return row;
  }

  function scrollToBottom() {
    var box = byId("psa-chat-messages");
    if (box) box.scrollTop = box.scrollHeight;
  }

  function appendUser(question) {
    appendRow("user", "<p>" + renderInline(escapeHtml(question)) + "</p>");
  }

  function appendBot(answer, sources, confidence, historyTurns) {
    var meta = CONFIDENCE_FA[confidence] || CONFIDENCE_FA.medium;
    var parts = ['<div class="psa-chat-answer psa-prose">' + renderMarkdown(answer) + "</div>"];

    var footer = [
      '<span class="psa-badge ' + meta.cls + '">' + escapeHtml(meta.label) + "</span>"
    ];
    if (historyTurns > 0) {
      footer.push(
        '<span class="psa-chat-hint">با در نظر گرفتن ' + faDigits(historyTurns) + " پیام قبلی</span>"
      );
    }
    parts.push('<div class="psa-chat-meta">' + footer.join("") + "</div>");

    if (sources && sources.length) {
      var chips = sources
        .map(function (source) {
          var sheets = (source.sheet_names || []).join(" ، ");
          var label = escapeHtml(source.file_key) + (sheets ? " — " + escapeHtml(sheets) : "");
          var title = source.snippet ? ' title="' + escapeHtml(source.snippet) + '"' : "";
          return '<span class="psa-chat-source"' + title + ">" + ICONS.file + " " + label + "</span>";
        })
        .join("");
      parts.push('<div class="psa-chat-sources"><span class="psa-help">منابع:</span>' + chips + "</div>");
    }

    return appendRow("bot", parts.join(""));
  }

  function appendNotice(text, isError) {
    return appendRow(
      "bot",
      '<p class="psa-chat-notice' + (isError ? " is-error" : "") + '">' + escapeHtml(text) + "</p>"
    );
  }

  function appendLoading() {
    return appendRow(
      "bot",
      '<p class="psa-chat-typing" aria-label="در حال تهیه پاسخ">' +
        "<span></span><span></span><span></span></p>",
      "is-loading"
    );
  }

  /* ==================================================================
     گفتگوها: ساخت / فهرست / حذف
     ================================================================== */
  function renderSessionList() {
    var list = byId("psa-chat-list");
    var empty = byId("psa-chat-list-empty");
    if (!list) return;

    list.innerHTML = state.sessions
      .map(function (chat) {
        var active = chat.id === state.sessionId;
        return (
          '<li class="psa-chat-item' + (active ? " is-active" : "") + '" data-session-id="' + chat.id + '">' +
          '<button type="button" class="psa-chat-item-main" data-psa-open="' + chat.id + '"' +
          (active ? ' aria-current="true"' : "") + ">" +
          '<span class="psa-chat-item-icon" aria-hidden="true">' + ICONS.chat + "</span>" +
          '<span class="psa-chat-item-text">' +
          '<span class="psa-chat-item-title">' + escapeHtml(chat.title) + "</span>" +
          '<span class="psa-chat-item-date">' + escapeHtml(formatDate(chat.updated_at)) + "</span>" +
          "</span></button>" +
          '<button type="button" class="psa-chat-item-delete" data-psa-delete="' + chat.id + '"' +
          ' aria-label="حذف گفتگوی ' + escapeHtml(chat.title) + '" title="حذف گفتگو">✕</button>' +
          "</li>"
        );
      })
      .join("");

    setHidden(empty, state.sessions.length > 0);
  }

  async function loadSessions() {
    var errorBox = byId("psa-chat-list-error");
    setHidden(errorBox, true);
    try {
      var response = await fetch(API_BASE + "/sessions");
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      var data = await response.json();
      state.sessions = data.sessions || [];
      renderSessionList();
      return state.sessions;
    } catch (err) {
      state.sessions = [];
      renderSessionList();
      if (errorBox) {
        errorBox.textContent = "دریافت فهرست گفتگوها ناموفق بود.";
        setHidden(errorBox, false);
      }
      window.psaShowToast(
        "دریافت فهرست گفتگوها ناموفق بود: " + (err && err.message ? err.message : String(err)),
        "error"
      );
      return [];
    }
  }

  async function createSession() {
    var titleInput = byId("psa-chat-new-title");
    var button = byId("psa-chat-new");
    var title = titleInput ? titleInput.value.trim() : "";

    if (button) button.disabled = true;
    try {
      var response = await fetch(API_BASE + "/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title || null })
      });
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      var chat = await response.json();
      if (titleInput) titleInput.value = "";
      await loadSessions();
      selectSession(chat.id);
      var input = byId("psa-chat-input");
      if (input) input.focus();
    } catch (err) {
      window.psaShowToast(
        "ساخت گفتگو ناموفق بود: " + (err && err.message ? err.message : String(err)),
        "error"
      );
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function deleteSession(sessionId) {
    if (!window.confirm("این گفتگو حذف شود؟ (محتوای گفتگو از قبل ذخیره نشده است)")) return;
    try {
      var response = await fetch(API_BASE + "/sessions/" + encodeURIComponent(sessionId), {
        method: "DELETE"
      });
      if (response.status !== 204 && !response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      if (state.sessionId === sessionId) clearActiveSession();
      await loadSessions();
      window.psaShowToast("گفتگو حذف شد.", "success");
    } catch (err) {
      window.psaShowToast(
        "حذف گفتگو ناموفق بود: " + (err && err.message ? err.message : String(err)),
        "error"
      );
    }
  }

  /* ==================================================================
     انتخاب/رهاکردن گفتگوی جاری
     ================================================================== */
  function setComposerEnabled(enabled) {
    var input = byId("psa-chat-input");
    var send = byId("psa-chat-send");
    if (input) input.disabled = !enabled;
    if (send) send.disabled = !enabled;
  }

  function clearActiveSession() {
    state.sessionId = null;
    state.history = [];
    storeSessionId(null);
    clearMessages();
    var box = byId("psa-chat-messages");
    if (box) {
      box.innerHTML =
        '<div class="psa-chat-placeholder" id="psa-chat-placeholder">' +
        ICONS.chat +
        '<p class="mt-2 text-sm font-semibold text-slate-700">گفتگویی انتخاب نشده است</p>' +
        '<p class="psa-help mt-1">از فهرست کنار یک گفتگو را انتخاب کنید یا «گفتگوی جدید» بزنید.</p>' +
        "</div>";
    }
    var title = byId("psa-chat-title");
    if (title) title.textContent = "گفتگویی انتخاب نشده است";
    var subtitle = byId("psa-chat-subtitle");
    if (subtitle) subtitle.textContent = "از فهرست کنار یک گفتگو را انتخاب کنید یا «گفتگوی جدید» بزنید.";
    setComposerEnabled(false);
    renderSessionList();
  }

  function selectSession(sessionId) {
    var chat = state.sessions.filter(function (item) {
      return item.id === sessionId;
    })[0];
    if (!chat) return;

    state.sessionId = chat.id;
    /* زمینه‌ی گفتگو هرگز بازیابی نمی‌شود: باز کردن یک گفتگو همیشه یک صفحه‌ی
       خالی است، چون هیچ پیامی ذخیره نشده است. */
    state.history = [];
    storeSessionId(chat.id);
    clearMessages();

    var title = byId("psa-chat-title");
    if (title) title.textContent = chat.title;
    var subtitle = byId("psa-chat-subtitle");
    if (subtitle) {
      subtitle.textContent =
        "ساخته‌شده در " + formatDate(chat.created_at) + " — محتوای گفتگو ذخیره نمی‌شود.";
    }

    appendRow(
      "bot",
      '<p class="psa-chat-notice">گفتگوی «' +
        escapeHtml(chat.title) +
        "» باز است. پاسخ‌ها از پایگاه‌دانش همین پروژه ساخته می‌شوند.</p>"
    );

    setComposerEnabled(true);
    renderSessionList();
    var input = byId("psa-chat-input");
    if (input) input.focus();
  }

  /* ==================================================================
     پرسش
     ================================================================== */
  function setBusy(busy) {
    state.busy = busy;
    var send = byId("psa-chat-send");
    var stop = byId("psa-chat-stop");
    setHidden(stop, !busy);
    if (send) send.disabled = busy;
    var input = byId("psa-chat-input");
    if (input) input.disabled = busy || state.sessionId === null;
  }

  /** آخرین N نوبت گفتگو -- تنها چیزی که همراه پرسش به سرور می‌رود. */
  function historyPayload() {
    return state.history.slice(-HISTORY_TURNS_SENT).map(function (turn) {
      return { role: turn.role, content: turn.content };
    });
  }

  function rememberTurn(role, content) {
    state.history.push({ role: role, content: content });
    if (state.history.length > CLIENT_HISTORY_LIMIT) {
      state.history = state.history.slice(-CLIENT_HISTORY_LIMIT);
    }
  }

  async function ask(question) {
    var loading = appendLoading();
    var controller = new AbortController();
    state.controller = controller;
    setBusy(true);

    // پرسش کاربر و زمینه‌ی قبلی همین حالا ثبت می‌شوند تا answered-history درست بماند.
    var sentHistory = historyPayload();
    rememberTurn("user", question);

    try {
      var response = await fetch(
        API_BASE + "/sessions/" + encodeURIComponent(state.sessionId) + "/ask",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: question, recent_history: sentHistory }),
          signal: controller.signal
        }
      );
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      var data = await response.json();

      if (loading) loading.remove();
      var answer = data.answer || "پاسخی دریافت نشد.";
      appendBot(answer, data.sources, data.confidence, data.history_turns_used);
      rememberTurn("assistant", answer);
    } catch (err) {
      if (loading) loading.remove();
      if (err && err.name === "AbortError") {
        appendNotice("درخواست متوقف شد.", false);
        // پرسش ناتمام از حافظه حذف می‌شود تا نوبت بعدی زمینه‌ی نادرست نگیرد.
        state.history.pop();
      } else {
        var message = err && err.message ? err.message : String(err);
        appendNotice(message, true);
        window.psaShowToast(message, "error");
        state.history.pop();
      }
    } finally {
      state.controller = null;
      setBusy(false);
      var input = byId("psa-chat-input");
      if (input) input.focus();
    }
  }

  function submitQuestion(event) {
    event.preventDefault();
    if (state.busy || !state.sessionId) return;
    var input = byId("psa-chat-input");
    if (!input) return;
    var question = input.value.trim();
    if (!question) {
      window.psaShowToast("متن پرسش را وارد کنید.", "warning");
      return;
    }
    input.value = "";
    ask(question);
  }

  /* ==================================================================
     اتصال رویدادها
     ================================================================== */
  function attach() {
    var form = byId("psa-chat-form");
    if (form) form.addEventListener("submit", submitQuestion);

    var input = byId("psa-chat-input");
    if (input) {
      input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          submitQuestion(event);
        }
      });
    }

    var newButton = byId("psa-chat-new");
    if (newButton) newButton.addEventListener("click", createSession);

    var newTitle = byId("psa-chat-new-title");
    if (newTitle) {
      newTitle.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          event.preventDefault();
          createSession();
        }
      });
    }

    var stopButton = byId("psa-chat-stop");
    if (stopButton) {
      stopButton.addEventListener("click", function () {
        if (state.controller) state.controller.abort();
      });
    }

    var list = byId("psa-chat-list");
    if (list) {
      list.addEventListener("click", function (event) {
        var openButton = event.target.closest("[data-psa-open]");
        if (openButton) {
          selectSession(Number(openButton.getAttribute("data-psa-open")));
          return;
        }
        var deleteButton = event.target.closest("[data-psa-delete]");
        if (deleteButton) {
          event.stopPropagation();
          deleteSession(Number(deleteButton.getAttribute("data-psa-delete")));
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", async function () {
    if (!byId("psa-chat-form")) return; // حالت دروازه‌بندی‌شده (بدون پایگاه‌دانش)
    attach();
    setComposerEnabled(false);

    var sessions = await loadSessions();
    var stored = readStoredSessionId();
    var known = sessions.some(function (chat) {
      return chat.id === stored;
    });
    if (stored !== null && known) {
      selectSession(stored);
    } else {
      clearActiveSession();
    }
  });
})();
