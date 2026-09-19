/**
 * منطق صفحه‌ی کارگاه «چت‌بات مالی» (گفتگوهای نام‌دار + پرسش/پاسخ ماندگار).
 *
 * ساختار کلی از همان الگوی ``budget_analysis.js``/``checklist.js`` پیروی می‌کند
 * (بدون هیچ وابستگی بیرونی و بدون مرحله‌ی build، آدرس‌ها از ``window.PSA_WORKSHOP``
 * که رجیستری تزریق کرده می‌آیند، و همه‌ی خطاها با ``window.psaShowToast`` نمایش
 * داده می‌شوند).
 *
 * چهار قرارداد مهم این فایل که باید حفظ شوند:
 *
 * ۱) **پیام کاربر همان لحظه رندر می‌شود.** پیش از این، صفحه فقط یک حباب «در حال
 *    تایپ» می‌ساخت و پرسش خودِ کاربر را هرگز نشان نمی‌داد (تابعش تعریف شده بود
 *    ولی هیچ‌جا صدا زده نمی‌شد) -- همان باگ گزارش‌شده. این‌جا پرسش بلافاصله
 *    (پیش از پاسخ سرور) راست‌چین رندر می‌شود، و به‌محض رسیدن پاسخ ``ask``،
 *    شناسه‌ی همان ردیف ذخیره‌شده روی همان عنصر گذاشته می‌شود. بنابراین نمایش
 *    «ارسال تازه» و «تاریچه‌ی بارگذاری‌شده از سرور» یکی است -- نه پیام تکراری،
 *    نه پیام گم‌شده.
 *
 * ۲) **تاریخچه از سرور می‌آید، نه از حافظه‌ی مرورگر.** با انتخاب هر گفتگو،
 *    ``GET /sessions/{id}/messages`` خوانده و همه‌ی پیام‌ها (پرسش‌ها، پاسخ‌های
 *    کامل، و پاسخ‌های در حال تولید) رندر می‌شوند. در ``localStorage`` فقط شناسه‌ی
 *    آخرین گفتگوی باز می‌ماند (متادیتا) -- هیچ متنی از گفتگو در مرورگر ذخیره
 *    نمی‌شود و زمینه‌ی چندنوبتی هم سمت سرور ساخته می‌شود.
 *
 * ۳) **پاسخ در پس‌زمینه ساخته می‌شود.** ``POST .../ask`` فوراً (۲۰۲) برمی‌گردد و
 *    تنها شناسه‌ی پیام‌ها را می‌دهد؛ سپس این فایل با یک بازه‌ی ثابت
 *    (``POLL_INTERVAL_MS``، همان ۱۵۰۰ میلی‌ثانیه‌ی ``checklist.js``/
 *    ``budget_analysis.js``) وضعیت همان پیام را polling می‌کند تا ``complete`` یا
 *    ``failed`` شود. هیچ AbortController ای به این درخواست‌ها گره نخورده است:
 *    رفتن به گفتگو/کارگاه دیگر فقط *polling سمت کلاینت* را متوقف می‌کند و کار
 *    سمت سرور را نمی‌کشد. با برگشتن به همان گفتگو، تاریخچه دوباره خوانده می‌شود
 *    (پاسخ تمام‌شده را نشان می‌دهد) و اگر پاسخی هنوز ``pending`` باشد، polling
 *    برای همان از سر گرفته می‌شود.
 *
 * ۴) **خروجی مدل هرگز به‌صورت HTML خام تزریق نمی‌شود.** مسیر امن این است:
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

  /* همان فاصله‌ی polling بقیه‌ی کارگاه‌ها. پاسخ یک پرسش چند ثانیه طول می‌کشد،
     پس یک درخواست سبک هر ۱.۵ ثانیه هزینه‌ی محسوسی ندارد. */
  var POLL_INTERVAL_MS = 1500;

  var CONFIDENCE_FA = {
    high: { label: "اطمینان بالا", cls: "psa-badge-success" },
    medium: { label: "اطمینان متوسط", cls: "psa-badge-warning" },
    needs_review: { label: "نیازمند بازبینی", cls: "psa-badge-danger" }
  };

  var PENDING_HINT_FA =
    "در حال پاسخ‌گویی… می‌توانید به گفتگو یا کارگاه دیگری بروید؛ پاسخ در پس‌زمینه ساخته می‌شود.";

  var ICONS = {
    user: '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    bot: '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v2"/><path d="m5 8 1.5 1.5"/><path d="M19 8l-1.5 1.5"/><rect x="4" y="9" width="16" height="10" rx="3"/><circle cx="9" cy="14" r="1"/><circle cx="15" cy="14" r="1"/></svg>',
    chat: '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/></svg>',
    file: '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><polyline points="14 2 14 8 20 8"/></svg>'
  };

  var state = {
    sessionId: null,
    sessions: [],
    /** شناسه‌ی پیام‌های در حال تولید پاسخ → عنصر همان ردیف (فقط همین گفتگو). */
    pending: {},
    pollTimer: null,
    posting: false,
    /* با هر بار عوض‌کردن گفتگو یک عدد بالا می‌رود تا پاسخ‌های دیرآمده‌ی یک
       درخواست قدیمی، در گفتگوی جدید رندر نشوند. */
    generation: 0
  };

  /* HTML اولیه‌ی حالت «گفتگوی خالی» که قالب رندر کرده است -- نگه داشته می‌شود تا
     اگر همه‌ی پیام‌ها پاک شدند، همان (و نه یک نسخه‌ی دست‌ساز) برگردد. */
  var emptyPlaceholderHtml = null;

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
      return raw ? String(raw) : null;
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

  function sessionsUrl() {
    return API_BASE + "/sessions";
  }

  function sessionUrl(sessionId) {
    return sessionsUrl() + "/" + encodeURIComponent(sessionId);
  }

  function messageUrl(sessionId, messageId) {
    return sessionUrl(sessionId) + "/messages/" + encodeURIComponent(messageId);
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
     رندر پیام‌ها
     ================================================================== */
  function messagesBox() {
    return byId("psa-chat-messages");
  }

  function hidePlaceholder() {
    var placeholder = byId("psa-chat-placeholder");
    if (placeholder) placeholder.remove();
  }

  function showEmptyPlaceholder() {
    var box = messagesBox();
    if (!box || !emptyPlaceholderHtml) return;
    box.innerHTML = emptyPlaceholderHtml;
  }

  /** فقط ردیف‌های پیام را پاک می‌کند (placeholder دست‌نخورده می‌ماند). */
  function clearRenderedMessages() {
    var box = messagesBox();
    if (!box) return;
    var rows = box.querySelectorAll(".psa-chat-row");
    for (var index = 0; index < rows.length; index += 1) {
      rows[index].remove();
    }
  }

  function scrollToBottom() {
    var box = messagesBox();
    if (box) box.scrollTop = box.scrollHeight;
  }

  function appendRow(role, innerHtml, extraClass, messageId) {
    var box = messagesBox();
    if (!box) return null;
    hidePlaceholder();
    var row = document.createElement("div");
    row.className = "psa-chat-row is-" + role + (extraClass ? " " + extraClass : "");
    if (messageId) row.setAttribute("data-message-id", messageId);
    row.innerHTML =
      '<span class="psa-chat-avatar is-' + role + '" aria-hidden="true">' +
      (role === "user" ? ICONS.user : ICONS.bot) +
      "</span>" +
      '<div class="psa-chat-bubble">' + innerHtml + "</div>";
    box.appendChild(row);
    scrollToBottom();
    return row;
  }

  function rowFor(messageId) {
    var box = messagesBox();
    if (!box || !/^[A-Za-z0-9_-]+$/.test(String(messageId || ""))) return null;
    return box.querySelector('[data-message-id="' + messageId + '"]');
  }

  function userBodyHtml(text) {
    return "<p>" + renderInline(escapeHtml(text)) + "</p>";
  }

  function botBodyHtml(message) {
    if (message.status === "failed") {
      var failure = message.error_message || "پاسخ‌دهی به این پرسش ناموفق بود.";
      return '<p class="psa-chat-notice is-error">' + escapeHtml(failure) + "</p>";
    }

    var parts = ['<div class="psa-chat-answer psa-prose">' + renderMarkdown(message.content || "") + "</div>"];

    var meta = CONFIDENCE_FA[message.confidence];
    if (meta) {
      parts.push(
        '<div class="psa-chat-meta">' +
          '<span class="psa-badge ' + meta.cls + '">' + escapeHtml(meta.label) + "</span>" +
          "</div>"
      );
    }

    var sources = message.sources || [];
    if (sources.length) {
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

    return parts.join("");
  }

  function pendingBodyHtml() {
    return (
      '<p class="psa-chat-typing mt-1" aria-label="در حال پاسخ‌گویی">' +
      "<span></span><span></span><span></span></p>" +
      '<p class="psa-chat-hint">' + escapeHtml(PENDING_HINT_FA) + "</p>"
    );
  }

  /**
   * یک پیام سرور را رندر می‌کند: اگر ردیفش از قبل وجود دارد فقط محتوایش را
   * به‌روز می‌کند، وگرنه ردیف تازه می‌سازد. همین یک تابع هم تاریخچه‌ی بارگذاری‌شده
   * و هم پاسخ تازه‌رسیده‌ی polling را می‌کشد، پس هیچ پیام تکراری ساخته نمی‌شود.
   */
  function renderMessage(message) {
    if (!message || !message.role) return null;
    var existing = message.id ? rowFor(message.id) : null;

    if (message.role === "user") {
      if (existing) {
        existing.querySelector(".psa-chat-bubble").innerHTML = userBodyHtml(message.content);
        return existing;
      }
      return appendRow("user", userBodyHtml(message.content), null, message.id);
    }

    if (message.status === "pending") {
      var pendingRow =
        existing || appendRow("bot", pendingBodyHtml(), "is-loading", message.id);
      if (pendingRow) registerPending(message.id, pendingRow);
      return pendingRow;
    }

    if (existing) {
      existing.classList.remove("is-loading");
      existing.querySelector(".psa-chat-bubble").innerHTML = botBodyHtml(message);
      unregisterPending(message.id);
      scrollToBottom();
      return existing;
    }
    var row = appendRow("bot", botBodyHtml(message), null, message.id);
    unregisterPending(message.id);
    return row;
  }

  function appendNotice(text, isError) {
    return appendRow(
      "bot",
      '<p class="psa-chat-notice' + (isError ? " is-error" : "") + '">' + escapeHtml(text) + "</p>"
    );
  }

  /* ==================================================================
     polling پاسخ‌های در حال تولید
     ================================================================== */
  function pendingIds() {
    return Object.keys(state.pending);
  }

  function registerPending(messageId, row) {
    if (!messageId) return;
    state.pending[messageId] = row || null;
    startPolling();
  }

  function unregisterPending(messageId) {
    if (!messageId) return;
    delete state.pending[messageId];
    if (!pendingIds().length) stopPolling();
  }

  function startPolling() {
    if (state.pollTimer || !pendingIds().length) return;
    state.pollTimer = window.setInterval(pollPending, POLL_INTERVAL_MS);
  }

  /**
   * توقف polling سمت کلاینت. **کار سمت سرور را نمی‌کشد** -- پاسخ در پس‌زمینه
   * ساخته می‌شود و با بازگشت به همین گفتگو، تاریخچه وضعیت واقعی را نشان می‌دهد
   * (و اگر هنوز ``pending`` باشد، polling از سر گرفته می‌شود).
   */
  function stopPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function pollPending() {
    var sessionId = state.sessionId;
    if (!sessionId || !pendingIds().length) {
      stopPolling();
      return;
    }
    var generation = state.generation;
    var ids = pendingIds();
    for (var index = 0; index < ids.length; index += 1) {
      await pollOne(sessionId, ids[index], generation);
    }
  }

  async function pollOne(sessionId, messageId, generation) {
    try {
      var response = await fetch(messageUrl(sessionId, messageId));
      if (response.status === 404) {
        // گفتگو/پیام دیگر وجود ندارد (مثلاً حذف شده) -- polling بی‌فایده است.
        unregisterPending(messageId);
        return;
      }
      if (!response.ok) return; // خطای موقت: دور بعد دوباره تلاش می‌شود
      var message = await response.json();
      if (generation !== state.generation || sessionId !== state.sessionId) return;
      if (message.status === "pending") return;
      renderMessage(message);
      // «آخرین استفاده» گفتگو تازه شده است؛ فهرست کنار را هم به‌روز نگه می‌داریم.
      loadSessions();
    } catch (err) {
      /* شبکه/سرور در این لحظه: دور بعد دوباره تلاش می‌شود. */
    }
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
      var response = await fetch(sessionsUrl());
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
      var response = await fetch(sessionsUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title || null })
      });
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      var chat = await response.json();
      if (titleInput) titleInput.value = "";
      await loadSessions();
      await selectSession(chat.id);
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
    if (!window.confirm("این گفتگو و کل پیام‌هایش حذف شود؟ این کار برگشت‌پذیر نیست.")) return;
    try {
      var response = await fetch(sessionUrl(sessionId), { method: "DELETE" });
      if (response.status !== 204 && !response.ok) {
        throw new Error(await parseErrorDetail(response));
      }
      if (state.sessionId === sessionId) showNoSessionSelected();
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

  /** وضعیت «هیچ گفتگویی انتخاب نشده است» -- بدون هیچ درخواستی به سرور. */
  function showNoSessionSelected() {
    state.generation += 1;
    state.sessionId = null;
    state.pending = {};
    stopPolling();
    storeSessionId(null);
    var box = messagesBox();
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

  async function loadMessages(sessionId) {
    var response = await fetch(sessionUrl(sessionId) + "/messages");
    if (!response.ok) throw new Error(await parseErrorDetail(response));
    var data = await response.json();
    var messages = data.messages || [];

    // اگر کاربر در همین فاصله گفتگو را عوض کرده باشد، این پاسخ دیگر مربوط نیست.
    if (state.sessionId !== sessionId) return;

    clearRenderedMessages();
    state.pending = {};
    stopPolling();
    if (!messages.length) {
      showEmptyPlaceholder();
      return;
    }
    messages.forEach(renderMessage);
  }

  /**
   * یک گفتگو را باز می‌کند: تاریخچه‌ی **ذخیره‌شده** را می‌خواند و رندر می‌کند،
   * و هر پیامی که هنوز ``pending`` است را دوباره به polling می‌سپارد (کاربر ممکن
   * است پرسشی را در یک گفتگوی دیگر یا حتی در یک بازدید قبلی فرستاده باشد).
   */
  async function selectSession(sessionId) {
    var chat = state.sessions.filter(function (item) {
      return item.id === sessionId;
    })[0];
    if (!chat) return;

    state.generation += 1;
    state.sessionId = chat.id;
    state.pending = {};
    stopPolling();
    storeSessionId(chat.id);
    clearRenderedMessages();
    setComposerEnabled(false);

    var title = byId("psa-chat-title");
    if (title) title.textContent = chat.title;
    var subtitle = byId("psa-chat-subtitle");
    if (subtitle) {
      subtitle.textContent =
        "ساخته‌شده در " + formatDate(chat.created_at) + " — آخرین استفاده " + formatDate(chat.updated_at);
    }

    try {
      await loadMessages(chat.id);
    } catch (err) {
      var message = err && err.message ? err.message : String(err);
      appendNotice(message, true);
      window.psaShowToast(message, "error");
    }

    setComposerEnabled(true);
    renderSessionList();
    var input = byId("psa-chat-input");
    if (input) input.focus();
  }

  /* ==================================================================
     پرسش
     ================================================================== */
  function setPosting(posting) {
    state.posting = posting;
    var send = byId("psa-chat-send");
    if (send) send.disabled = posting;
  }

  async function ask(question) {
    var sessionId = state.sessionId;
    if (!sessionId) return;

    // ۱) رندر آنی پرسش کاربر -- پیش از آنکه حتی درخواست برود. این دقیقاً همان
    //    باگی است که گزارش شده بود: پیام کاربر باید همان لحظه دیده شود.
    var optimisticRow = appendRow("user", userBodyHtml(question));
    setPosting(true);

    try {
      var response = await fetch(sessionUrl(sessionId) + "/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question })
      });
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      var data = await response.json();

      // ۲) شناسه‌ی ردیف ذخیره‌شده روی همان حباب می‌نشیند: از این لحظه، این پیام
      //    همان پیامی است که تاریخچه‌ی سرور هم برمی‌گرداند (بدون تکرار).
      if (optimisticRow && data.user_message_id) {
        optimisticRow.setAttribute("data-message-id", data.user_message_id);
      }
      if (state.sessionId !== sessionId) return; // کاربر گفتگو را عوض کرده است

      // ۳) حباب «در حال پاسخ‌گویی» برای ردیف دستیار + شروع polling.
      var pendingRow = appendRow(
        "bot",
        pendingBodyHtml(),
        "is-loading",
        data.assistant_message_id
      );
      registerPending(data.assistant_message_id, pendingRow);
    } catch (err) {
      // پرسش هرگز ثبت نشد: حباب خوش‌بینانه نباید بماند.
      if (optimisticRow) optimisticRow.remove();
      var message = err && err.message ? err.message : String(err);
      if (state.sessionId === sessionId) {
        appendNotice(message, true);
        window.psaShowToast(message, "error");
      }
    } finally {
      setPosting(false);
      var input = byId("psa-chat-input");
      if (input) input.focus();
    }
  }

  function submitQuestion(event) {
    event.preventDefault();
    if (state.posting || !state.sessionId) return;
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

    var list = byId("psa-chat-list");
    if (list) {
      list.addEventListener("click", function (event) {
        var openButton = event.target.closest("[data-psa-open]");
        if (openButton) {
          selectSession(openButton.getAttribute("data-psa-open"));
          return;
        }
        var deleteButton = event.target.closest("[data-psa-delete]");
        if (deleteButton) {
          event.stopPropagation();
          deleteSession(deleteButton.getAttribute("data-psa-delete"));
        }
      });
    }

    // با ترک صفحه، فقط polling سمت کلاینت متوقف می‌شود؛ پرسش‌های در جریان روی
    // سرور تمام می‌شوند و با بازگشت به همین گفتگو نتیجه‌شان دیده می‌شود.
    window.addEventListener("pagehide", stopPolling);
  }

  document.addEventListener("DOMContentLoaded", async function () {
    if (!byId("psa-chat-form")) return; // حالت دروازه‌بندی‌شده (بدون پایگاه‌دانش)
    emptyPlaceholderHtml = byId("psa-chat-placeholder")
      ? byId("psa-chat-placeholder").outerHTML
      : null;
    attach();
    setComposerEnabled(false);

    var sessions = await loadSessions();
    var stored = readStoredSessionId();
    var known = sessions.some(function (chat) {
      return chat.id === stored;
    });
    if (stored !== null && known) {
      await selectSession(stored);
    } else {
      showNoSessionSelected();
    }
  });
})();
