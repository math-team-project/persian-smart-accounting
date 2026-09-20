"""تست‌های سامانهٔ طراحی (توکن‌ها، پوستهٔ تیره، کنتراست، جهت).

چرا این فایل وجود دارد:
    پیش از این، رنگ‌ها در دو جا تکرار می‌شدند (``base.html`` با هگز و
    ``app.css`` با توکن‌های ``--color-*``)، هیچ تستی محتوای CSS را نمی‌خواند، و
    هیچ تستی کنتراست را نمی‌سنجید. دو کامنت در کد به
    ``tests/test_dark_theme.py`` ارجاع می‌دادند که وجود نداشت.

    این تست‌ها همان شکاف را می‌بندند. هر موردی که این‌جا پاس داشته می‌شود،
    مستقیماً یک نقص واقعی بود: آواتار گفت‌وگو در پوستهٔ تیره ۱.۴۴:۱ می‌داد،
    متن راهنمای ورودی ۲.۵۶:۱، جدول‌های عریض در موبایل بریده می‌شدند، و حاشیهٔ
    ورودی‌ها زیر آستانهٔ ۳:۱ بود.

    تست‌ها فقط فایل می‌خوانند و CSS را تجزیه می‌کنند -- بدون مرورگر و بدون
    وابستگی تازه. تنها استثنا تست سرو شدن ``tokens.css`` است که از fixture
    ``client`` استفاده می‌کند.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from api.main import WEB_DIR

CSS_DIR = Path(WEB_DIR, "static", "css")
TOKENS_CSS = CSS_DIR / "tokens.css"
APP_CSS = CSS_DIR / "app.css"
TEMPLATES_DIR = Path(WEB_DIR, "templates")
BASE_TEMPLATE = TEMPLATES_DIR / "base.html"


# ===========================================================================
# ابزارهای تجزیه
# ===========================================================================

def _strip_comments(text: str) -> str:
    """حذف کامنت‌های CSS و Jinja.

    لازم است چون توضیحات این پروژه خودشان هگز و ``var()`` مثال می‌زنند (مثلاً
    «#0f172a روی سطح #121b2c») و در غیر این صورت تست «رنگ خام» را بی‌دلیل
    می‌شکنند. تست باید *اعلان‌ها* را بسنجد، نه نثر توضیحات را.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return text


def _parse_rules(css: str) -> dict[str, dict[str, str]]:
    """CSS را به نگاشت «مسیر سلکتور → اعلان‌ها» تبدیل می‌کند.

    قاعده‌های داخل ``@media`` با مسیر کامل کلید می‌خورند
    (``@media (max-width: 480px) .psa-x``) تا بتوان وجود/نبود یک اعلان را در
    یک مدیای مشخص سنجید. برای فایل‌های این پروژه کافی است: هیچ CSS تودرتوی
    دیگری (``@supports``، ``@layer``) وجود ندارد.
    """
    css = _strip_comments(css)
    rules: dict[str, dict[str, str]] = {}
    stack: list[str] = []
    buf = ""
    for ch in css:
        if ch == "{":
            stack.append(buf.strip())
            buf = ""
        elif ch == "}":
            if stack:
                stack.pop()
            buf = ""
        elif ch == ";":
            if stack:
                prop, sep, value = buf.partition(":")
                if sep:
                    key = " ".join(stack)
                    rules.setdefault(key, {})[prop.strip()] = value.strip()
            buf = ""
        else:
            buf += ch
    return rules


def _decls(rules: dict[str, dict[str, str]], selector: str) -> dict[str, str]:
    assert selector in rules, f"قاعدهٔ «{selector}» در CSS پیدا نشد"
    return rules[selector]


def _token_contexts() -> tuple[dict[str, str], dict[str, str]]:
    """(توکن‌های پوستهٔ روشن، توکن‌های پوستهٔ تیره).

    پوستهٔ تیره روی روشن سوار می‌شود: هر توکنی که در بلوک تیره بازتعریف نشده
    باشد، مقدار روشنِ ``:root`` را نگه می‌دارد -- دقیقاً همان‌طور که مرورگر
    محاسبه می‌کند.
    """
    rules = _parse_rules(TOKENS_CSS.read_text(encoding="utf-8"))
    light: dict[str, str] = {}
    dark_overrides: dict[str, str] = {}
    for selector, decls in rules.items():
        for prop, value in decls.items():
            if not prop.startswith("--"):
                continue
            if ":root" in selector:
                light[prop] = value
            elif "[data-theme=" in selector and "dark" in selector:
                dark_overrides[prop] = value
    assert light, "هیچ توکنی در :root تعریف نشده است"
    assert dark_overrides, "بلوک پوستهٔ تیره هیچ توکنی بازتعریف نمی‌کند"
    return light, {**light, **dark_overrides}


def _resolve(value: str, ctx: dict[str, str], depth: int = 0) -> str:
    """``var(--x)`` را تا رسیدن به مقدار ثابت باز می‌کند."""
    assert depth < 12, f"زنجیرهٔ var() تودرتو/حلقه‌دار: {value}"

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        assert name in ctx, f"توکن تعریف‌نشده: {name}"
        return _resolve(ctx[name], ctx, depth + 1)

    return re.sub(r"var\(\s*(--[a-zA-Z0-9-]+)\s*\)", repl, value)


def _to_rgb(value: str) -> tuple[float, float, float, float]:
    """رنگ ثابت را به ``(r, g, b, alpha)`` تبدیل می‌کند (۰..۲۵۵ و ۰..۱)."""
    value = value.strip()
    hex_match = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value)
    if hex_match:
        raw = hex_match.group(1)
        if len(raw) == 3:
            raw = "".join(c * 2 for c in raw)
        return (
            int(raw[0:2], 16),
            int(raw[2:4], 16),
            int(raw[4:6], 16),
            1.0,
        )
    rgba_match = re.fullmatch(
        r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?\s*\)",
        value,
    )
    assert rgba_match, f"رنگ قابل تجزیه نیست: {value!r}"
    r, g, b = (float(rgba_match.group(i)) for i in (1, 2, 3))
    alpha = rgba_match.group(4)
    return (r, g, b, 1.0 if alpha is None else float(alpha))


def _composite(fg: tuple[float, float, float, float], bg: tuple[float, float, float, float]):
    """ترکیب رنگ نیمه‌شفاف روی پس‌زمینه (همان کاری که مرورگر می‌کند)."""
    a = fg[3]
    return (
        fg[0] * a + bg[0] * (1 - a),
        fg[1] * a + bg[1] * (1 - a),
        fg[2] * a + bg[2] * (1 - a),
        1.0,
    )


def _luminance(rgb: tuple[float, float, float, float]) -> float:
    def channel(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(rgb[i]) for i in range(3))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg: str, bg: str, ctx: dict[str, str], backdrop: str) -> float:
    """نسبت کنتراست دو *مقدار* CSS، با ترکیب شفافیت روی توکن ``backdrop``.

    هر دو مقدار اول با ``var()`` باز می‌شوند. ``backdrop`` نام توکن است، نه
    مقدار -- چون رنگ‌های نیمه‌شفاف (پس‌زمینهٔ نشان‌ها) در واقع روی سطح کارت
    می‌نشینند و بدون ترکیب، کنتراست واقعی به دست نمی‌آید.
    """
    backdrop_rgb = _to_rgb(_resolve(ctx[backdrop], ctx))
    f = _to_rgb(_resolve(fg, ctx))
    b = _to_rgb(_resolve(bg, ctx))
    if f[3] < 1.0:
        f = _composite(f, backdrop_rgb)
    if b[3] < 1.0:
        b = _composite(b, backdrop_rgb)
    lighter, darker = max(_luminance(f), _luminance(b)), min(_luminance(f), _luminance(b))
    return (lighter + 0.05) / (darker + 0.05)


def _ratio_tokens(fg_token: str, bg_token: str, ctx: dict[str, str]) -> float:
    """نسبت کنتراست دو *توکن* (سطح مقایسه = همان پس‌زمینه)."""
    return _ratio(ctx[fg_token], ctx[bg_token], ctx, backdrop=bg_token)


LIGHT, DARK = _token_contexts()


# ===========================================================================
# ۱) یک منبع حقیقت برای رنگ
# ===========================================================================

def test_new_stylesheet_is_served(client):
    response = client.get("/static/css/tokens.css")
    assert response.status_code == 200, "tokens.css سرو نمی‌شود"
    assert response.content


def test_tokens_stylesheet_loads_before_app_stylesheet():
    """ترتیب حیاتی است.

    ``app.css`` نام‌های قدیمی ``--color-*`` را به توکن‌های ``tokens.css`` پل
    می‌زند؛ اگر برعکس بارگذاری شوند، در بهترین حالت کار می‌کند و در بدترین
    حالت (اگر روزی مقادیری هم‌نام شوند) بی‌صدا خراب می‌شود. چون هیچ مرحلهٔ build
    و باندل‌کردنی وجود ندارد، ترتیب فقط با همین دو تگ ``link`` تعیین می‌شود.
    """
    html = BASE_TEMPLATE.read_text(encoding="utf-8")
    tokens_at = html.find("/static/css/tokens.css")
    app_at = html.find("/static/css/app.css")
    assert tokens_at != -1, "base.html به tokens.css لینک نمی‌دهد"
    assert app_at != -1, "base.html به app.css لینک نمی‌دهد"
    assert tokens_at < app_at, "tokens.css باید پیش از app.css بارگذاری شود"


def test_tailwind_config_reads_from_tokens_not_hex():
    """پیکربندی Tailwind نباید پالت را دوباره با هگز تعریف کند.

    پیش‌تر همین پالت (۲۴ مقدار ``brand``/``navy``/``gold``) هم در ``base.html``
    با هگز بود و هم در ``app.css`` با توکن -- دو منبع حقیقت که ناگهان با هم
    فرق داشتند (``navy-50`` تا ``navy-400`` واقعاً فرق داشتند).
    """
    config = _strip_comments(
        (TEMPLATES_DIR / "components" / "tailwind_config.html").read_text(encoding="utf-8")
    )
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", config)
    assert not hexes, f"رنگ خام در پیکربندی Tailwind: {sorted(set(hexes))}"
    # و واقعاً به توکن‌ها وصل باشد (نه اینکه فقط خالی شده باشد)
    assert config.count("var(--psa-") >= 40, "پیکربندی به توکن‌ها وصل نیست"


def test_browser_chrome_colour_matches_the_palette():
    """``<meta name="theme-color">`` تنها هگز مجاز در قالب‌هاست -- و باید هم‌خوان بماند.

    محتوای یک متا تگ نمی‌تواند ``var()`` بخواند، بنابراین رنگ نوار مرورگر در
    موبایل ناچار یک مقدار ثابت است. این تست نگه می‌دارد که آن ثابت با توکن
    ``--psa-background`` (و مقدار اولیهٔ ``--psa-navy-900``) هم‌خوان بماند، تا
    روزی که پوسته عوض شود، نوار مرورگر عقب نماند.

    رنگ‌های درون نقاشی SVG (لوگوی برداری سربرگ/پابرگ، با ``fill=``/``stroke=``)
    استثنا هستند: آن‌ها تصویرند، نه رنگ رابط، و نباید توکنی شوند.
    """
    html = BASE_TEMPLATE.read_text(encoding="utf-8")
    artwork = {
        "#" + value.lower()
        for value in re.findall(r'(?:fill|stroke)="#([0-9a-fA-F]{6})"', html)
    }
    literals = {value.lower() for value in re.findall(r"#[0-9a-fA-F]{6}\b", html)} - artwork

    allowed = {
        _resolve(LIGHT["--psa-background"], LIGHT).lower(),
        _resolve(DARK["--psa-background"], DARK).lower(),
        _resolve(LIGHT["--psa-navy-900"], LIGHT).lower(),
    }
    assert literals <= allowed, f"رنگ خام ناشناخته در base.html: {sorted(literals - allowed)}"


def test_every_referenced_custom_property_is_defined():
    """هر ``var(--x)`` به یک توکن تعریف‌شده اشاره کند.

    این تست مهم‌ترین محافظ مهاجرت است: ``var(--نام‌ناشناخته)`` بدون مقدار
    جانشین، خودِ ویژگی را بی‌اعتبار می‌کند و مرورگر هیچ خطایی نشان نمی‌دهد.
    """
    defined: set[str] = set()
    for path in (TOKENS_CSS, APP_CSS):
        defined |= set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", _strip_comments(path.read_text(encoding="utf-8"))))
    # توکن‌های داخل قالب‌ها (استایل درون‌خطی/متغیرهای Tailwind) هم معتبرند
    for path in TEMPLATES_DIR.rglob("*.html"):
        defined |= set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", _strip_comments(path.read_text(encoding="utf-8"))))

    referenced: dict[str, str] = {}
    sources = [TOKENS_CSS, APP_CSS, *TEMPLATES_DIR.rglob("*.html"),
               *(Path(WEB_DIR) / "js").glob("*.js")]
    for path in sources:
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for name in re.findall(r"var\(\s*(--[a-zA-Z0-9-]+)\s*\)", text):
            referenced.setdefault(name, str(path.relative_to(WEB_DIR.parent)))

    missing = {name: where for name, where in referenced.items() if name not in defined}
    assert not missing, f"توکن‌های تعریف‌نشده: {missing}"


# ===========================================================================
# ۲) پوستهٔ تیره فقط توکن‌ها را عوض می‌کند
# ===========================================================================

def test_dark_theme_overrides_only_custom_properties():
    """قاعدهٔ معماری مستندشده: بلوک پوستهٔ تیره هیچ قاعدهٔ کامپوننتی تکرار نمی‌کند.

    تنها استثنای مستند، بخش «نگاشت یوتیلیتی‌های رنگی Tailwind» است: قالب‌ها
    رنگ‌ها را بدون پیشوند ``dark:`` نوشته‌اند، پس آن چند کلاس باید در پوستهٔ
    تیره با یک سلکتور مرکب بازنویسی شوند. آن قاعده‌ها ``[data-theme="dark"]
    .کلاس`` هستند و این‌جا (که سلکتور دقیقاً خود بلوک توکن است) بررسی نمی‌شوند.
    """
    offenders = []
    checked = 0
    for path in (TOKENS_CSS, APP_CSS):
        rules = _parse_rules(path.read_text(encoding="utf-8"))
        for selector, decls in rules.items():
            if selector.strip() != '[data-theme="dark"]':
                continue
            checked += 1
            for prop, value in decls.items():
                if prop.startswith("--") or prop == "color-scheme":
                    continue
                offenders.append(f"{path.name} :: {prop}: {value}")
    assert checked >= 1, "بلوک خالص [data-theme=\"dark\"] پیدا نشد"
    assert not offenders, f"ویژگی غیرتوکنی در بلوک تیره: {offenders}"


def test_every_state_foreground_token_has_a_dark_pair():
    """هر ``--psa-state-*-fg`` باید جفت تیره داشته باشد.

    رنگی که فقط برای پوستهٔ روشن تعریف شود، در پوستهٔ تیره همان مقدار تیره را
    نگه می‌دارد و روی سطح تیره ناخوانا می‌شود -- دقیقاً همان اشتباهی که آواتار
    گفت‌وگو را به ۱.۴۴:۱ رساند.
    """
    rules = _parse_rules(TOKENS_CSS.read_text(encoding="utf-8"))
    dark: set[str] = set()
    for selector, decls in rules.items():
        if "[data-theme=" in selector and "dark" in selector:
            dark |= set(decls)
    light_state = {
        prop
        for selector, decls in rules.items()
        if ":root" in selector
        for prop in decls
        if re.fullmatch(r"--psa-state-.+-fg", prop)
    }
    assert light_state, "هیچ توکن وضعیتی تعریف نشده است"
    assert not (light_state - dark), f"بدون جفت تیره: {sorted(light_state - dark)}"


# ===========================================================================
# ۳) رنگ خام ممنوع (جز tokens.css)
# ===========================================================================

# خطوطی که هگز در آن‌ها بخشی از یک «ماسک» است، نه رنگ: در
# ``mask-image`` مقدار ``#000`` یعنی «کاملاً مات» و رنگ نیست.
_RAW_COLOUR_ALLOWED_SIGNATURES = ("mask-image",)


def test_no_raw_colour_in_shared_styles_and_partials():
    """در CSS مشترک و جزئی‌های کامپوننت هیچ هگز خامی نماند.

    هگز خام یعنی رنگی که پوستهٔ تیره نمی‌تواند عوضش کند -- منبع اصلی همان
    نقص‌هایی که این تغییر رفع کرد (نوار یافتهٔ #dc2626، حاشیهٔ ردیف جدول،
    پس‌زمینهٔ آواتار). ``tokens.css`` تنها جای مجاز تعریف رنگ است.

    قالب‌های صفحه در این بررسی نیستند: ``base.html`` یک لوگوی SVG برداری‌شده
    با ~۱۰۰ رنگ نقاشی دارد (``fill=``/``stroke=``) که تصویر است، نه رنگ رابط،
    و توکنی‌کردن آن اشتباه است. هگز متا تگ هم در تست جداگانه پاس داشته می‌شود.
    """
    offenders = []
    targets = [APP_CSS, *sorted((TEMPLATES_DIR / "components").glob("*.html"))]
    for path in targets:
        for lineno, line in enumerate(
            _strip_comments(path.read_text(encoding="utf-8")).split("\n"), 1
        ):
            if any(sig in line for sig in _RAW_COLOUR_ALLOWED_SIGNATURES):
                continue
            for found in re.findall(r"#[0-9a-fA-F]{3,8}\b", line):
                offenders.append(f"{path.relative_to(WEB_DIR)}:{lineno} -> {found}")
    assert not offenders, "رنگ خام بیرون از tokens.css: " + "; ".join(offenders)


# ===========================================================================
# ۴) کنتراست
# ===========================================================================

# (توکن متن، توکن پس‌زمینه، آستانه، توضیح)
TEXT_PAIRS = [
    ("--psa-text-heading", "--psa-surface", 4.5, "عنوان روی کارت"),
    ("--psa-text", "--psa-surface", 4.5, "متن اصلی روی کارت"),
    ("--psa-text-strong", "--psa-surface", 4.5, "متن ثانویه"),
    ("--psa-text-soft", "--psa-surface", 4.5, "متن کم‌رنگ"),
    ("--psa-text-muted", "--psa-surface", 4.5, "متن راهنما/متادیتا"),
    ("--psa-state-conform-fg", "--psa-surface", 4.5, "نشان «تطابق دارد»"),
    ("--psa-state-nonconform-fg", "--psa-surface", 4.5, "نشان «عدم تطابق»"),
    ("--psa-state-processing-fg", "--psa-surface", 4.5, "نشان «خطای پردازش»"),
    ("--psa-state-review-fg", "--psa-surface", 4.5, "نشان «بررسی دستی»"),
    ("--psa-state-neutral-fg", "--psa-surface", 4.5, "نشان خنثی"),
    ("--psa-state-info-fg", "--psa-surface", 4.5, "نشان اطلاع‌رسانی"),
    ("--psa-text-on-accent", "--psa-state-nonconform-solid", 4.5, "متن روی دکمهٔ خطر"),
    ("--psa-gold-ink", "--psa-gold-500", 4.5, "متن روی دکمهٔ طلایی"),
    # جفتِ وارونه: دکمهٔ پابرگ و آواتار کاربر
    ("--psa-background", "--psa-text-heading", 4.5, "متن روی دکمهٔ وارونه/آواتار"),
    # آواتار ربات: همان جفتی که پیش‌تر ۱.۴۴:۱ بود
    ("--psa-background", "--psa-state-processing-fg", 4.5, "متن روی آواتار ربات"),
]

# عناصر غیرمتنی: آستانهٔ ۳:۱ (معیار ۱.۴.۱۱).
NON_TEXT_PAIRS = [
    ("--psa-focus", "--psa-surface", 3.0, "حلقهٔ فوکوس"),
    ("--psa-border-control", "--psa-surface", 3.0, "حاشیهٔ ورودی (۱.۴.۱۱)"),
    ("--psa-state-nonconform-bar", "--psa-surface", 3.0, "نوار یافتهٔ حسابرسی"),
]


@pytest.mark.parametrize("theme,ctx", [("روشن", LIGHT), ("تیره", DARK)])
@pytest.mark.parametrize("fg,bg,minimum,note", TEXT_PAIRS)
def test_text_contrast_meets_wcag(theme, ctx, fg, bg, minimum, note):
    ratio = _ratio_tokens(fg, bg, ctx)
    assert ratio >= minimum, f"[{theme}] {note}: {fg} روی {bg} = {ratio:.2f}:1 (کمینه {minimum})"


@pytest.mark.parametrize("theme,ctx", [("روشن", LIGHT), ("تیره", DARK)])
@pytest.mark.parametrize("fg,bg,minimum,note", NON_TEXT_PAIRS)
def test_non_text_contrast_meets_wcag(theme, ctx, fg, bg, minimum, note):
    ratio = _ratio_tokens(fg, bg, ctx)
    assert ratio >= minimum, f"[{theme}] {note}: {fg} روی {bg} = {ratio:.2f}:1 (کمینه {minimum})"


def test_muted_text_tokens_are_lighter_than_faint():
    """``faint`` عمداً تزئینی است و باید از ``muted`` روشن‌تر (کم‌کنتراست‌تر) بماند.

    این تست آن مرز را مستند می‌کند: اگر کسی روزی ``faint`` را برای متن واقعی
    به‌کار برد، تست کنتراست بالا آن را نمی‌گیرد (چون در فهرست نیست)، ولی این
    تست نشان می‌دهد ``faint`` هرگز جایگزین ``muted`` نبوده است.
    """
    faint = _ratio_tokens("--psa-text-faint", "--psa-surface", LIGHT)
    muted = _ratio_tokens("--psa-text-muted", "--psa-surface", LIGHT)
    assert faint < 4.5, f"faint باید تزئینی بماند، اما {faint:.2f}:1 است"
    assert muted >= 4.5, f"muted باید خوانا باشد، اما {muted:.2f}:1 است"


def test_placeholder_and_search_icon_are_not_decorative():
    """متن راهنمای ورودی و آیکون جست‌وجو باید کنتراست متن داشته باشند.

    هر دو پیش‌تر ``--color-faint`` بودند: ۲.۵۶:۱ روی سفید. placeholder متنی
    است که کاربر واقعاً می‌خواند (مثلاً «مثلاً پارک علم و فناوری خراسان») و
    آیکون جست‌وجو هم معنای فیلد را می‌رساند، پس هر دو آستانهٔ ۴.۵:۱ دارند.
    """
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    for selector, prop in (
        (".psa-input::placeholder", "color"),
        (".psa-search .psa-search-icon", "color"),
    ):
        value = _decls(rules, selector)[prop]
        ratio = _ratio(value, LIGHT["--psa-surface"], LIGHT, backdrop="--psa-surface")
        assert ratio >= 4.5, f"{selector} = {ratio:.2f}:1"


def test_faint_token_is_not_used_as_body_text_colour():
    """``--color-faint`` نباید رنگ متن جایی باشد که همان سطح روشن را دارد."""
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    offenders = [
        selector
        for selector, decls in rules.items()
        if decls.get("color", "").strip() == "var(--color-faint)"
    ]
    assert not offenders, f"«{offenders}» از توکن کم‌کنتراست برای متن استفاده می‌کند"


# ===========================================================================
# ۵) جهت و راست‌به‌چپ
# ===========================================================================

_PHYSICAL_PATTERNS = [    (r"(?:^|[;{\s])margin-(?:left|right)\s*:", "margin-left/right"),
    (r"(?:^|[;{\s])padding-(?:left|right)\s*:", "padding-left/right"),
    (r"(?:^|[;{\s])border-(?:left|right)(?:-[a-z]+)?\s*:", "border-left/right"),
    (r"(?:^|[;{\s])(?:left|right)\s*:", "left/right (inset)"),
    (r"text-align\s*:\s*(?:left|right)\b", "text-align: left/right"),
]


def test_no_physical_direction_properties():
    """در CSS فقط ویژگی‌های منطقی به‌کار روند (``inline-start``/``start``).

    سامانه تک‌جهته و راست‌به‌چپ است، پس ``margin-left`` همیشه اشتباه است و
    ``text-align: left`` همیشه فرض غلطی از LTR. برای متن لاتین (شناسه، بلوک کد)
    راه درست ``direction: ltr`` + ``unicode-bidi: isolate`` + ``text-align:
    start`` است که هیچ ویژگی فیزیکی‌ای باقی نمی‌گذارد.
    """
    offenders = []
    for path in (APP_CSS, TOKENS_CSS):
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for pattern, label in _PHYSICAL_PATTERNS:
            for match in re.finditer(pattern, text):
                lineno = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{lineno} [{label}]")
    assert not offenders, "ویژگی فیزیکی جهت: " + ", ".join(offenders)


# گرادیان‌های افقی که عمداً متقارن‌اند و جهت برایشان بی‌معناست.
_SYMMETRIC_GRADIENT_SIGNATURES = (
    "var(--bg-grid-ink) 1px",      # شبکهٔ پس‌زمینه: کاشی تکرارشوندهٔ ۱px
    "var(--color-track) 25%",      # درخشش اسکلتون: رنگ شروع و پایان یکی است
)


def test_directional_gradients_do_not_assume_ltr():
    """گرادیان‌های افقی نباید ``90deg``/``to right`` باشند.

    ``90deg`` در CSS همیشه چپ→راست است و به ``direction`` احترام نمی‌گذارد؛
    نوار پیشرفت در RTL از راست پر می‌شود، پس گرادیان برعکس خوانده می‌شد. راه
    درست توکن ``--psa-gradient-*`` با زاویهٔ ``to left`` است.
    """
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    offenders = []
    for selector, decls in rules.items():
        for prop, value in decls.items():
            if "linear-gradient" not in value:
                continue
            if "var(--psa-gradient" in value:
                continue
            if any(sig in value for sig in _SYMMETRIC_GRADIENT_SIGNATURES):
                continue
            if re.search(r"\b(?:90|270)deg\b|to (?:right|left)\b", value):
                offenders.append(f"{selector} [{prop}] {value[:70]}")
    assert not offenders, "گرادیان جهت‌دار بدون توکن: " + "; ".join(offenders)


def test_document_root_is_rtl_persian():
    html = BASE_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"<html[^>]*>", html)
    assert match, "تگ <html> پیدا نشد"
    tag = match.group(0)
    assert 'dir="rtl"' in tag, tag
    assert 'lang="fa"' in tag, tag


def test_theme_key_contract_is_stable():
    """قرارداد ``psa.theme`` که کامنت ``base.html`` به آن ارجاع می‌دهد.

    پیش‌تر آن کامنت به ``tests/test_dark_theme.py`` اشاره می‌کرد که وجود نداشت؛
    این تست همان قرارداد را واقعاً می‌پاید (نام کلید و مقادیر مجاز).
    """
    html = BASE_TEMPLATE.read_text(encoding="utf-8")
    assert '"psa.theme"' in html, "کلید پوسته در base.html پیدا نشد"
    assert '"dark"' in html and '"light"' in html, "مقادیر مجاز پوسته پیدا نشد"
    assert 'setAttribute("data-theme"' in html, "ویژگی پوسته روی <html> ست نمی‌شود"


# ===========================================================================
# ۶) تایپوگرافی و اجزای مشترک -- محافظ رگرسیون
# ===========================================================================

def test_body_text_floor_is_sixteen_pixels():
    """متن بدنه هرگز زیر ۱۶px نمی‌رود.

    پیش‌تر ``body { font-size: 15px }`` بود و روی موبایل به ۱۴px کاهش می‌یافت،
    در حالی که کاربران این سامانه حسابرسان و اعضای هیئت‌مدیره‌اند. این تست هم
    مقدار فعلی و هم *نبود* کاهش موبایل را قفل می‌کند.
    """
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    body_size = _decls(rules, "body")["font-size"]
    assert body_size == "var(--psa-text-base)", body_size
    assert _resolve(LIGHT["--psa-text-base"], LIGHT) == "1rem"

    for selector, decls in rules.items():
        if "max-width: 480px" in selector and selector.endswith(" body"):
            assert "font-size" not in decls, f"کف متن روی موبایل شکسته شده: {selector}"


def test_small_text_tokens_never_go_below_twelve_pixels():
    """کف پلهٔ کوچک ۱۲px است (پیش‌تر تا ۱۰.۸۸px می‌رسید)."""
    for token in ("--psa-text-2xs", "--psa-text-xs", "--psa-text-sm",
                  "--psa-text-base", "--psa-text-lg", "--psa-text-xl"):
        raw = _resolve(LIGHT[token], LIGHT)
        match = re.fullmatch(r"([\d.]+)rem", raw)
        assert match, f"{token} = {raw!r} (باید rem باشد)"
        px = float(match.group(1)) * 16
        assert px >= 12, f"{token} = {px}px"


def test_wide_tables_can_scroll_instead_of_being_clipped():
    """جدول‌های عریض باید اسکرول افقی بگیرند.

    ``.psa-table { min-width: 32rem }`` است، پس روی موبایل هر جدولی عریض‌تر از
    عرض صفحه می‌شود. با ``overflow: hidden`` ستون‌های انتهایی کاملاً ناپدید
    می‌شدند؛ ماتریس انحرافات بودجه و جدول یافته‌ها دقیقاً همین‌طور بودند.
    """
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    overflow = _decls(rules, ".psa-table-wrap")
    assert overflow.get("overflow-x") == "auto", overflow


def test_focus_visible_is_never_removed_without_a_replacement():
    """هر قاعده‌ای که ``outline`` را حذف می‌کند باید جانشین دیده‌شدنی بدهد.

    راهنمای «Focus States»: حذف کادر پیش‌فرض بدون جانشین ممنوع. ``--ring``
    قبلی تیل با آلفای ۲۴٪ بود که روی دکمهٔ تیل‌رنگ نامرئی می‌شد؛ جانشین درست
    ``--psa-shadow-focus`` است (حلقهٔ دولایه).
    """
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    checked = 0
    for selector, decls in rules.items():
        if "focus-visible" not in selector and "focus" not in selector:
            continue
        if decls.get("outline", "").strip() != "none":
            continue
        checked += 1
        shadow = decls.get("box-shadow", "")
        assert "shadow-focus" in shadow or "focus" in shadow, (
            f"{selector} کادر فوکوس را حذف کرده اما جانشین دیده‌شدنی ندارد: {shadow!r}"
        )
    assert checked >= 3, f"تنها {checked} قاعدهٔ حذف‌کنندهٔ outline بررسی شد"


def test_input_border_is_tokenised_for_theme_switching():
    """حاشیهٔ ورودی باید توکنی باشد که در پوستهٔ تیره هم کنتراست کافی دارد."""
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    border = _decls(rules, ".psa-input")["border"]
    assert "var(--psa-border-control)" in border, border
    for theme, ctx in (("روشن", LIGHT), ("تیره", DARK)):
        ratio = _ratio_tokens("--psa-border-control", "--psa-surface", ctx)
        assert ratio >= 3.0, f"[{theme}] حاشیهٔ ورودی = {ratio:.2f}:1"


def test_chat_avatar_is_readable_in_both_themes():
    """آواتار گفت‌وگو در پوستهٔ تیره ناپدید/ناخوانا نباشد.

    پیش‌تر پس‌زمینه‌اش ``--color-primary`` ثابت بود: #0f172a روی سطح تیره
    #121b2c یعنی ۱.۰۶:۱. برای آواتار ربات هم ``--color-gold-deep`` پس‌زمینه
    بود که در پوستهٔ تیره #fcd34d می‌شد و متن سفید روی آن ۱.۴۴:۱ -- این دومین
    مورد، بدترین کنتراست کل سامانه بود.
    """
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    base = _decls(rules, ".psa-chat-avatar")
    bot = _decls(rules, ".psa-chat-avatar.is-bot")
    for theme, ctx in (("روشن", LIGHT), ("تیره", DARK)):
        for name, decls in (("آواتار کاربر", base), ("آواتار ربات", bot)):
            fg = decls["color"] if "color" in decls else base["color"]
            bg = decls["background"]
            ratio = _ratio(fg, bg, ctx, "--psa-surface")
            assert ratio >= 4.5, f"[{theme}] {name} = {ratio:.2f}:1"


def test_touch_targets_are_enlarged_only_on_coarse_pointers():
    """هدف لمسی ۴۴px زیر ``pointer: coarse`` -- و نه روی دسکتاپ."""
    rules = _parse_rules(APP_CSS.read_text(encoding="utf-8"))
    coarse = [sel for sel in rules if "pointer: coarse" in sel]
    assert coarse, "بلوک @media (pointer: coarse) پیدا نشد"
    joined = " ".join(coarse)
    for expected in (".psa-btn-sm", ".psa-chip", ".psa-theme-btn", ".psa-toast-close"):
        assert expected in joined, f"{expected} در هدف لمسی نیست"
    assert not any("pointer: coarse" in sel for sel in rules if "body" in sel)
