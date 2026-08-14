"""
استایل‌های سفارشی داشبورد — تم تیره (Dark Mode)، فونت فارسی، چیدمان
راست‌به‌چپ (RTL) و اصلاح مشکلات کنتراست/همپوشانی کامپوننت‌های بومی Streamlit.

نکته: تم پایه (رنگ‌های اصلی ویجت‌های بومی مثل دکمه‌ها، فایل‌آپلودر، اینپوت‌ها)
از طریق .streamlit/config.toml تنظیم شده تا Streamlit خودش کنتراست صحیح را
برای اجزای داخلی محاسبه کند. این فایل صرفا ظرافت‌های بصری، فونت فارسی، RTL و
کامپوننت‌های سفارشی (هدر، کارت‌ها، نشان‌ها، KPI) را اضافه می‌کند.
"""

# --- پالت رنگی تم تیره ------------------------------------------------------
BG_BASE = "#0F172A"
BG_ELEVATED = "#1E293B"
BG_ELEVATED_2 = "#16213A"
BORDER = "#334155"
ACCENT = "#10B981"        # زمردی - CTA اصلی
ACCENT_INDIGO = "#6366F1"
ACCENT_SKY = "#0EA5E9"
ACCENT_PURPLE = "#8B5CF6"
DANGER = "#EF4444"
MUTED_BADGE = "#64748B"
TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#94A3B8"

CUSTOM_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;600;700;800&display=swap');

/* اصلاح باگ شناخته‌شده: اگر override سراسری font-family بر روی المان‌های آیکون
   بومی Streamlit (span[data-testid="stIconMaterial"]) هم اعمال شود، نام لیگاچر آیکون
   (مثلا keyboard_double_arrow_left) به‌جای شکل بصری، به‌صورت متن خام نمایش داده
   می‌شود. این قاعده با اولویت بالاتر (specificity) فونت اصلی Material Symbols
   را برای این المان‌ها بازمی‌گرداند (فونت به‌صورت محلی توسط خود Streamlit بارگذاری می‌شود). */
span[data-testid="stIconMaterial"] {{
    font-family: 'Material Symbols Rounded' !important;
    font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24 !important;
    font-size: 1.15rem !important;
    line-height: 1 !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    white-space: nowrap;
    word-wrap: normal;
    direction: ltr !important;
}}

html, body, [class*="css"], .stApp, .stMarkdown, .stText, p, span, div, h1, h2, h3, h4, h5, h6,
button, input, textarea, label, li, table, th, td {{
    font-family: 'Vazirmatn', 'Tahoma', sans-serif !important;
}}

:root {{
    --psa-bg: {BG_BASE};
    --psa-surface: {BG_ELEVATED};
    --psa-surface-2: {BG_ELEVATED_2};
    --psa-border: {BORDER};
    --psa-accent: {ACCENT};
    --psa-indigo: {ACCENT_INDIGO};
    --psa-sky: {ACCENT_SKY};
    --psa-purple: {ACCENT_PURPLE};
    --psa-danger: {DANGER};
    --psa-muted-badge: {MUTED_BADGE};
    --psa-text: {TEXT_PRIMARY};
    --psa-text-secondary: {TEXT_SECONDARY};
}}

.stApp {{
    direction: rtl;
    background:
        radial-gradient(circle at 10% -10%, rgba(16, 185, 129, 0.10), transparent 40%),
        radial-gradient(circle at 90% 0%, rgba(99, 102, 241, 0.12), transparent 45%),
        var(--psa-bg);
    color: var(--psa-text);
}}

section.main > div {{
    direction: rtl;
}}

.block-container {{
    padding-top: 1.6rem;
    padding-bottom: 3rem;
    max-width: 1220px;
}}

h1, h2, h3, h4, h5, h6 {{
    color: var(--psa-text) !important;
}}

p, .psa-muted, small, .stCaption, [data-testid="stCaptionContainer"] {{
    color: var(--psa-text-secondary);
}}

hr, [data-testid="stDivider"] {{
    border-color: var(--psa-border) !important;
}}

a {{ color: var(--psa-sky); }}

/* =========================================================================
   نوار کناری (Sidebar) — RTL کامل + کنتراست تیره
   ========================================================================= */
[data-testid="stSidebar"] {{
    direction: rtl;
    background: linear-gradient(180deg, #111c33 0%, var(--psa-bg) 100%);
    border-left: 1px solid var(--psa-border);
}}
[data-testid="stSidebar"] * {{
    text-align: right;
}}
[data-testid="stSidebar"] .psa-sidebar-brand {{
    display: flex;
    align-items: center;
    gap: .6rem;
    margin-bottom: .2rem;
}}
[data-testid="stSidebar"] .psa-sidebar-brand .psa-icon-badge {{
    background: linear-gradient(135deg, var(--psa-accent), var(--psa-sky));
    color: #05221a;
    border-radius: 12px;
    width: 38px;
    height: 38px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}}
[data-testid="stSidebar"] .psa-sidebar-title {{
    font-weight: 800;
    font-size: 1.02rem;
    color: var(--psa-text);
    line-height: 1.3;
}}
[data-testid="stSidebar"] .psa-sidebar-subtitle {{
    font-size: .78rem;
    color: var(--psa-text-secondary);
}}
[data-testid="stSidebar"] ul {{
    padding-right: 0;
    margin: 0;
    list-style: none;
}}
[data-testid="stSidebar"] .psa-step {{
    display: flex;
    align-items: flex-start;
    gap: .6rem;
    padding: .5rem 0;
    border-bottom: 1px dashed rgba(148, 163, 184, 0.18);
}}
[data-testid="stSidebar"] .psa-step:last-child {{ border-bottom: none; }}
[data-testid="stSidebar"] .psa-step-num {{
    flex-shrink: 0;
    width: 24px;
    height: 24px;
    border-radius: 8px;
    background: rgba(16, 185, 129, 0.14);
    color: var(--psa-accent);
    font-weight: 700;
    font-size: .78rem;
    display: flex;
    align-items: center;
    justify-content: center;
}}
[data-testid="stSidebar"] .psa-step-text {{
    font-size: .83rem;
    color: var(--psa-text-secondary);
    line-height: 1.6;
}}

/* دکمه‌ی جمع‌کردن/بازکردن سایدبار */
[data-testid="stSidebarCollapseButton"] {{
    color: var(--psa-text) !important;
}}
[data-testid="stSidebarCollapseButton"] button {{
    background: var(--psa-surface) !important;
    border: 1px solid var(--psa-border) !important;
    border-radius: 10px !important;
}}

/* =========================================================================
   متریک‌های بومی Streamlit (fallback)
   ========================================================================= */
[data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stMetricDelta"] {{
    direction: rtl;
    text-align: right;
}}

/* =========================================================================
   هدر اصلی (Hero) — یکپارچه با پس‌زمینه‌ی تیره، بدون بنر روشن
   ========================================================================= */
.psa-hero {{
    background: linear-gradient(135deg, #0b3a2f 0%, #0f2f4d 55%, #1b1440 100%);
    border: 1px solid var(--psa-border);
    border-radius: 22px;
    padding: 2rem 2.2rem;
    margin-bottom: 1.6rem;
    position: relative;
    overflow: hidden;
    box-shadow: 0 18px 45px -22px rgba(0, 0, 0, 0.65);
}}
.psa-hero::after {{
    content: "";
    position: absolute;
    inset: 0;
    background: radial-gradient(circle at 88% -20%, rgba(16, 185, 129, 0.28), transparent 55%),
                radial-gradient(circle at -10% 120%, rgba(99, 102, 241, 0.22), transparent 50%);
    pointer-events: none;
}}
.psa-hero-top {{
    display: flex;
    align-items: center;
    gap: .85rem;
    position: relative;
    margin-bottom: .65rem;
}}
.psa-hero-badge {{
    width: 52px;
    height: 52px;
    flex-shrink: 0;
    border-radius: 16px;
    background: linear-gradient(135deg, var(--psa-accent), var(--psa-sky));
    color: #05221a;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 10px 24px -8px rgba(16, 185, 129, 0.55);
}}
.psa-hero h1 {{
    font-size: 1.7rem;
    font-weight: 800;
    margin: 0;
    color: var(--psa-text) !important;
}}
.psa-hero p.psa-hero-desc {{
    font-size: .96rem;
    color: var(--psa-text-secondary);
    margin: 0 0 1rem 0;
    max-width: 48rem;
    position: relative;
    line-height: 1.9;
}}
.psa-hero .psa-badges {{
    display: flex;
    gap: .55rem;
    flex-wrap: wrap;
    position: relative;
}}
.psa-chip {{
    display: inline-flex;
    align-items: center;
    gap: .4rem;
    background: rgba(248, 250, 252, 0.06);
    border: 1px solid rgba(148, 163, 184, 0.28);
    color: var(--psa-text);
    border-radius: 999px;
    padding: .32rem .9rem;
    font-size: .8rem;
    font-weight: 500;
    backdrop-filter: blur(6px);
}}
.psa-chip .psa-icon {{ color: var(--psa-accent); }}

/* =========================================================================
   عنوان بخش‌ها
   ========================================================================= */
.psa-section-title {{
    font-size: 1.08rem;
    font-weight: 800;
    color: var(--psa-text);
    margin: .3rem 0 .9rem 0;
    display: flex;
    align-items: center;
    gap: .5rem;
}}
.psa-section-title .psa-icon {{ color: var(--psa-accent); }}

/* =========================================================================
   کارت‌های بارگذاری فایل
   ========================================================================= */
/* قالب کارت خود کانتینر بومی st.container(border=True) است که رنگ و حاشیه‌اش
   از طریق .streamlit/config.toml (تم تیره) به‌صورت بومی تأمین می‌شود. */
div[data-testid="stVerticalBlock"]:has(> div > div.psa-upload-marker) {{
    transition: filter .2s ease;
}}
div[data-testid="stVerticalBlock"]:has(> div > div.psa-upload-marker):hover {{
    filter: brightness(1.08);
}}

.psa-upload-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: .5rem;
    margin-bottom: .15rem;
}}
.psa-upload-title {{
    display: flex;
    align-items: center;
    gap: .5rem;
    font-weight: 700;
    font-size: .92rem;
    color: var(--psa-text);
}}
.psa-upload-title .psa-icon {{ color: var(--psa-sky); }}

.psa-badge {{
    display: inline-flex;
    align-items: center;
    gap: .28rem;
    font-size: .68rem;
    font-weight: 700;
    padding: .18rem .55rem;
    border-radius: 999px;
    white-space: nowrap;
}}
.psa-badge.required {{ background: rgba(239, 68, 68, 0.16); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.35); }}
.psa-badge.optional {{ background: rgba(100, 116, 139, 0.2); color: #cbd5e1; border: 1px solid rgba(100, 116, 139, 0.4); }}

.psa-badge.uploaded {{ background: rgba(16, 185, 129, 0.16); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.4); }}
.psa-badge.empty {{ background: rgba(148, 163, 184, 0.12); color: var(--psa-text-secondary); border: 1px solid rgba(148, 163, 184, 0.25); }}

.psa-upload-help {{
    font-size: .78rem;
    color: var(--psa-text-secondary);
    margin: .1rem 0 .55rem 0;
    line-height: 1.6;
}}

/* --- استایل بومی فایل‌آپلودر Streamlit -----------------------------------
   از سلکتورهای عمومی‌تر (به‌جای testidهای داخلی غیرقابل‌اطمینان) استفاده می‌شود
   تا در نسخه‌های مختلف Streamlit هم مقاوم باشد. */
[data-testid="stFileUploader"] {{ direction: rtl; }}
[data-testid="stFileUploader"] * {{ color: var(--psa-text); }}
[data-testid="stFileUploader"] small {{ color: var(--psa-text-secondary) !important; }}

[data-testid="stFileUploaderDropzone"] {{
    background: rgba(15, 23, 42, 0.55) !important;
    border: 1.5px dashed var(--psa-border) !important;
    border-radius: 14px !important;
    transition: border-color .2s ease, background .2s ease;
}}
[data-testid="stFileUploaderDropzone"]:hover {{
    border-color: var(--psa-accent) !important;
    background: rgba(16, 185, 129, 0.06) !important;
}}
[data-testid="stFileUploaderDropzone"] svg {{
    fill: var(--psa-text-secondary) !important;
    color: var(--psa-text-secondary) !important;
}}
[data-testid="stFileUploaderDropzoneInstructions"] {{
    color: var(--psa-text) !important;
    direction: rtl;
}}
[data-testid="stFileUploaderDropzoneInstructions"] span {{
    color: var(--psa-text) !important;
    font-size: .85rem;
}}
[data-testid="stFileUploaderDropzoneInstructions"] small {{
    color: var(--psa-text-secondary) !important;
}}

/* دکمه‌ی «انتخاب فایل» و هر دکمه‌ی ثانویه‌ی دیگر داخل کارت آپلود */
[data-testid="stFileUploader"] button {{
    background: var(--psa-surface) !important;
    color: var(--psa-text) !important;
    border: 1px solid var(--psa-border) !important;
    border-radius: 10px !important;
}}
[data-testid="stFileUploader"] button:hover {{
    border-color: var(--psa-accent) !important;
    color: var(--psa-accent) !important;
}}

/* ردیف فایل بارگذاری‌شده (نام فایل، حجم، دکمه‌ی حذف) که خارج از dropzone رندر می‌شود */
[data-testid="stFileUploader"] > section > div:not([data-testid="stFileUploaderDropzone"]) {{
    background: var(--psa-surface-2);
    border: 1px solid var(--psa-border);
    border-radius: 10px;
    margin-top: .5rem;
}}
[data-testid="stFileUploader"] [title="Remove file"], [data-testid="stFileUploader"] button[kind="icon"] {{
    color: var(--psa-text-secondary) !important;
}}
[data-testid="stFileUploader"] [title="Remove file"]:hover, [data-testid="stFileUploader"] button[kind="icon"]:hover {{
    color: var(--psa-danger) !important;
}}

/* =========================================================================
   دکمه‌ها
   ========================================================================= */
.stButton > button, .stDownloadButton > button {{
    border-radius: 12px;
    font-weight: 700;
    padding: .6rem 1.4rem;
    transition: transform .15s ease, box-shadow .15s ease, filter .15s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    transform: translateY(-1px);
}}
.stButton > button[kind="primary"] {{
    background: linear-gradient(135deg, var(--psa-accent) 0%, #0ea472 100%) !important;
    border: none !important;
    box-shadow: 0 12px 28px -12px rgba(16, 185, 129, 0.6);
}}
.stButton > button[kind="primary"]:hover {{
    box-shadow: 0 16px 34px -12px rgba(16, 185, 129, 0.75);
    filter: brightness(1.05);
}}
.stButton > button[kind="secondary"], .stDownloadButton > button {{
    background: var(--psa-surface) !important;
    border: 1px solid var(--psa-border) !important;
    color: var(--psa-text) !important;
}}
.stButton > button[kind="secondary"]:hover, .stDownloadButton > button:hover {{
    border-color: var(--psa-danger) !important;
    color: #fca5a5 !important;
}}

/* =========================================================================
   کارت‌های KPI
   ========================================================================= */
.psa-kpi {{
    border-radius: 16px;
    padding: 1rem 1.1rem;
    color: white;
    height: 100%;
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 14px 30px -20px rgba(0,0,0,.6);
}}
.psa-kpi .psa-kpi-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: .5rem;
    opacity: .9;
}}
.psa-kpi .psa-kpi-value {{
    font-size: 1.75rem;
    font-weight: 800;
    line-height: 1.1;
}}
.psa-kpi .psa-kpi-label {{
    font-size: .8rem;
    opacity: .92;
    margin-top: .3rem;
}}
.psa-kpi.total {{ background: linear-gradient(135deg,#1e293b,#0f172a); }}
.psa-kpi.pass  {{ background: linear-gradient(135deg,#059669,#065f46); }}
.psa-kpi.fail  {{ background: linear-gradient(135deg,#dc2626,#7f1d1d); }}
.psa-kpi.error {{ background: linear-gradient(135deg,#d97706,#78350f); }}
.psa-kpi.manual{{ background: linear-gradient(135deg,#475569,#1e293b); }}
.psa-kpi.rate  {{ background: linear-gradient(135deg,#0ea5e9,#0369a1); }}

/* =========================================================================
   نشان وضعیت سوالات چک‌لیست
   ========================================================================= */
.psa-status-badge {{
    display: inline-flex;
    align-items: center;
    gap: .35rem;
    font-size: .78rem;
    font-weight: 700;
    padding: .25rem .7rem;
    border-radius: 999px;
    color: white;
}}

/* =========================================================================
   جعبه‌های اعلان سفارشی (به‌جای پوشه/پونز)
   ========================================================================= */
.psa-alert {{
    display: flex;
    align-items: flex-start;
    gap: .7rem;
    border-radius: 14px;
    padding: .9rem 1.1rem;
    border: 1px solid var(--psa-border);
    background: var(--psa-surface);
    margin-bottom: 1rem;
}}
.psa-alert .psa-icon {{ flex-shrink: 0; margin-top: .1rem; }}
.psa-alert.info {{ border-color: rgba(14, 165, 233, 0.4); background: rgba(14, 165, 233, 0.08); }}
.psa-alert.info .psa-icon {{ color: var(--psa-sky); }}
.psa-alert.warning {{ border-color: rgba(245, 158, 11, 0.4); background: rgba(245, 158, 11, 0.08); }}
.psa-alert.warning .psa-icon {{ color: #f59e0b; }}
.psa-alert-text {{ color: var(--psa-text); font-size: .88rem; line-height: 1.7; }}

/* =========================================================================
   عمومی
   ========================================================================= */
.psa-muted {{ color: var(--psa-text-secondary); font-size: .85rem; }}

.psa-footer {{
    text-align: center;
    color: var(--psa-text-secondary);
    font-size: .78rem;
    margin-top: 2.4rem;
    padding-top: 1rem;
    border-top: 1px dashed var(--psa-border);
}}

div[data-testid="stExpander"] {{
    border-radius: 14px !important;
    border: 1px solid var(--psa-border) !important;
    background: var(--psa-surface) !important;
    box-shadow: 0 6px 18px -16px rgba(0,0,0,.7);
}}
div[data-testid="stExpander"] summary {{
    color: var(--psa-text) !important;
}}

.stTabs [data-baseweb="tab-list"] {{
    gap: .4rem;
    border-bottom: 1px solid var(--psa-border);
}}
.stTabs [data-baseweb="tab"] {{
    border-radius: 10px 10px 0 0;
    padding: .5rem 1.1rem;
    font-weight: 600;
    color: var(--psa-text-secondary);
}}
.stTabs [aria-selected="true"] {{
    color: var(--psa-accent) !important;
}}

[data-testid="stDataFrame"] {{
    border: 1px solid var(--psa-border);
    border-radius: 12px;
    overflow: hidden;
}}

code, .stCode, [data-testid="stCode"] {{
    direction: ltr;
    text-align: left;
}}
</style>
"""


def inject_custom_css(st) -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
