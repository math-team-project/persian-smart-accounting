"""ساخت پرامپت مرحله‌ی ۲ (تنها جایی که مدل زبانی وارد می‌شود).

ساختار: یک پیام ``system`` که «قواعد» را می‌گوید و یک پیام ``user`` که «داده» را
می‌آورد. قواعد همان اصول پرامپت مرجع هستند، فقط فشرده و بدون تکرار -- اما هیچ
قاعده‌ی ماهوی حذف نشده است: عدم جعل داده، ردیابی کامل، تحلیل سه‌لایه، واژگان
وضعیت/اهمیت، قاعده‌ی ضد کلی‌گویی، ممنوعیت نسبت‌دادن علت بدون شاهد، و طراحی
توسعه‌پذیر معیارها (فهرست محورها از ``criteria`` خوانده می‌شود، نه هاردکد).

داده‌ی ورودی (خروجی مرحله‌ی ۱) به‌صورت فهرست ردیف‌های «فرم ← بخش ← ردیف ←
مقادیر ستون‌ها + محل» رندر می‌شود تا هر عددی که مدل در ماتریس خطادهی می‌نویسد،
قابل ارجاع به محل دقیق خود در سند باشد.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Sequence

from budget_analysis import MISSING_TEXT, NOT_COMPUTABLE_TEXT
from budget_analysis.config import BudgetConfig
from budget_analysis.criteria import criteria_prompt_block
from budget_analysis.forms import form_name
from budget_analysis.schemas import DocumentExtraction, ExtractedCell, ExtractionBundle

__all__ = [
    "MAX_PROMPT_DATA_CHARS",
    "MAX_ROWS_PER_FORM",
    "MIN_DOCUMENT_CHARS",
    "RenderedDataBlock",
    "build_messages",
    "build_system_prompt",
    "build_user_prompt",
    "render_data_block",
    "render_documents_for_prompt",
]

# سقف ردیف‌های رندرشده از هر فرم -- تا پرامپت از اندازه‌ی مدل بیرون نزند.
MAX_ROWS_PER_FORM = 150

# سقف کل نویسه‌های بخش داده. این مقدار یک «دریچه‌ی ایمنی» برای ورودی‌های
# غیرعادی است، نه هدف: دو سند بودجه‌ی تفصیلی واقعی (نمونه‌های ۱۴۰۴ و ۱۴۰۵)
# حدود ۱۹۰ هزار نویسه داده تولید می‌کنند، پس سقف باید بالای این اندازه باشد
# وگرنه سند دوم بی‌سروصدا از پرامپت حذف می‌شود. برای مدل‌های با پنجره‌ی
# محدودتر می‌توان این مقدار را با متغیر محیطی زیر کم کرد.
MAX_PROMPT_DATA_CHARS = 400_000

# کف نویسه‌ی هر سند: حتی وقتی بودجه‌ی داده کم می‌شود، هیچ سندی به‌طور کامل حذف
# نمی‌شود و سرصفحه‌ی آن (نام فایل، سال، واحد، فهرست فرم‌ها) همیشه به مدل می‌رسد.
MIN_DOCUMENT_CHARS = 2_000

_ENV_MAX_PROMPT_CHARS = "PSA_BUDGET_MAX_PROMPT_CHARS"


def prompt_data_char_budget() -> int:
    """سقف نویسه‌ی بخش داده (پیش‌فرض ماژول، قابل بازنویسی با متغیر محیطی).

    مقدار نامعتبر/غیرمثبت نادیده گرفته می‌شود تا یک متغیر محیطی اشتباه، اجرا را
    به پرامپت خالی تبدیل نکند.
    """
    raw = os.environ.get(_ENV_MAX_PROMPT_CHARS)
    if raw:
        try:
            value = int(float(raw))
        except ValueError:
            value = 0
        if value > MIN_DOCUMENT_CHARS:
            return value
    return MAX_PROMPT_DATA_CHARS


_HOW_TO_REPORT_MISSING = (
    f"برای هر داده‌ای که در اسناد نیست عبارت «{MISSING_TEXT}» و برای هر محاسبه‌ای که "
    f"به‌دلیل فقدان داده ممکن نیست عبارت «{NOT_COMPUTABLE_TEXT}» را دقیقاً و بدون تغییر بنویس. "
    "این دو عبارت متن *نتیجه* و *شواهد* هستند و هرگز در فیلد ``status`` نمی‌آیند: "
    "وضعیت هر معیاری که داده‌اش کافی نیست همیشه «فاقد داده کافی» است."
)


def plain_number(value: float) -> str:
    """عدد با ارقام لاتین و جداکننده‌ی هزارگان برای *پیام مدل*.

    برخلاف متن گزارش نهایی (که ارقام فارسی می‌خواهد و رندر مرحله‌ی ۳ آن‌ها را
    می‌سازد)، داده‌ای که به مدل داده می‌شود با ارقام لاتین نوشته می‌شود تا محاسبه و
    مقایسه‌ی عددی بدون ابهام انجام شود.
    """
    number = float(value)
    return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"


def build_system_prompt(config: BudgetConfig) -> str:
    """پیام ``system``: قواعد تحلیل، واژگان، محورها و قالب خروجی."""
    thresholds = config.to_prompt_dict()
    threshold_lines = "\n".join(f"- {key}: {value}" for key, value in thresholds.items())

    return f"""تو سامانه‌ی تخصصی **پایش، تحلیل و ارزیابی مدیریتی بودجه‌ی پارک‌های علم و فناوری** هستی.
خروجی تو یک گزارش مدیریتی دقیق، عددی و قابل‌ردیابی برای هیأت امنا و مدیران ارشد است.
هدف، فقط یافتن خطای عددی نیست؛ باید مشخص شود مشکل دقیقاً کجاست، اندازه‌اش چقدر است،
چرا اهمیت دارد و درباره‌اش چه تصمیمی باید گرفته شود.

# اصول بنیادی (نقض آن‌ها نتیجه را بی‌اعتبار می‌کند)

**اصل ۱ ـ فقط اسناد مبنا.** فقط از داده‌های همین پیام استفاده کن. داده‌ی بیرونی، قانون،
آیین‌نامه یا مصوبه‌ای که در اسناد نیست، مرجع نتیجه قرار نمی‌گیرد؛ اگر نتیجه به منبع بیرونی
نیاز دارد آن را «نیازمند بررسی بر مبنای منبع بیرونی» علامت بزن و هرگز آستانه‌ای را
«الزام قانونی» معرفی نکن. آستانه‌های زیر **پارامتر سیاستی/مدیریتی سامانه** هستند، نه حکم قانونی.

**اصل ۲ ـ عدم حدس و جعل.** هیچ مبلغ، درصد، نرخ رشد، عنوان ردیف، شماره فرم، وضعیت یا نتیجه‌ای
بدون پشتوانه‌ی داده تولید نکن. {_HOW_TO_REPORT_MISSING}
هرگز مقدار مفقود را با صفر جایگزین نکن، مگر اینکه سند صراحتاً صفر نشان دهد.

**اصل ۳ ـ ردیابی کامل.** هر یافته باید فرم، بخش، ردیف و ستون محل داده را داشته باشد.
شناسایی معنایی بر موقعیت سلول اولویت دارد؛ اگر ساختار ردیف/ستون در دو سال تفاوت دارد،
معادل معنایی را از روی عنوان، سلسله‌مراتب، سرصفحه، واحد، سال و محتوا پیدا کن. شماره‌ی
ردیف اکسل فقط وقتی قابل اعتماد است که همان ساختار در دیتای ورودی دیده شود.

**اصل ۴ ـ تحلیل سه‌لایه.** ابتدا ارزیابی هر معیار، سپس ثبت ریزدانه‌ی نتیجه در ماتریس
خطادهی، و در نهایت گزارش مدیریتی از روی **همان ماتریس**. هرگز گزارش را مستقیماً از
تفسیر آزاد اسناد نساز.

**اصل ۵ ـ ممنوعیت کلی‌گویی.** عبارت‌های «برخی»، «بعضی»، «قابل توجه»، «بالا»، «پایین»،
«غیرعادی»، «نیازمند بررسی» و «ریسک بالا» تنها وقتی مجازند که بلافاصله با عدد و محل همراه
شوند: دقیقاً چه چیزی؟ کجا؟ مقدار چقدر؟ حد مجاز چقدر؟ انحراف چقدر؟
نمونه‌ی نامناسب: «برخی اقلام رشدی بالایی داشته‌اند.»
نمونه‌ی مناسب: «در فرم ۵-۱، ردیف [عنوان]، مبلغ از X میلیون ریال در سال پایه به Y میلیون
ریال در سال جاری رسیده، رشد Z درصد بوده و Z−۴۰ واحد درصد از سقف ۴۰٪ فراتر رفته است.»

**اصل ۶ ـ علت فقط با شاهد.** اگر علت مغایرت در سند ذکر نشده، آن را به سوءمدیریت یا هر علت
دیگری نسبت نده؛ بنویس علت در اسناد صریح نیست و نیازمند شفاف‌سازی است. اگر علت در توضیحات
سند آمده، همان را با ارجاع به مستند بیان کن.

**اصل ۷ ـ زبان مدیریتی.** از واژگان «محور پایش، معیار ارزیابی، شاخص، مغایرت، انحراف، هشدار
مدیریتی، ریسک، یافته بااهمیت، مورد نیازمند بررسی، مورد نیازمند شفاف‌سازی، اقدام پیشنهادی،
تصمیم پیشنهادی» استفاده کن. شناسه‌های فنی (C1، C2، ...) فقط در ماتریس خطادهی می‌آیند و در
متن خلاصه مدیریتی و یافته‌ها نمایش داده نمی‌شوند.

# نوع مقایسه

هر معیار باید صراحتاً نوع مقایسه‌ی خود را در فیلد ``comparison_type`` مشخص کند؛ آن را از
روی عنوان معیار حدس نزن. مقادیر مجاز:
۱) بودجه جاری در مقابل عملکرد سال پایه
۲) اعتبار مصوب در مقابل عملکرد واقعی
۳) عملکرد سال پایه در مقابل بودجه سال جاری
۴) اصلاحیه در مقابل بودجه اولیه
۵) شاخص جاری در مقابل شاخص مرجع
همچنین مقادیر ``base_document``، ``comparison_document``، ``base_year`` و ``comparison_year``
را پر کن.

# واژگان مجاز (خروجی خارج از این واژه‌ها پذیرفته نمی‌شود)

وضعیت: عادی | نزدیک به حد | نیازمند بررسی | هشدار مدیریتی | مغایرت بااهمیت | ریسک بااهمیت | فاقد داده کافی | معاف
اهمیت: عادی | قابل توجه | بااهمیت | بسیار بالا
وضعیت و اهمیت را از هم جدا نگه دار.

مقدار فیلدهای ``status`` و ``importance`` باید **عیناً** یکی از همین رشته‌ها باشد؛
هیچ عبارت دیگری -- حتی اگر در «قاعده ارزیابی» یک معیار آمده باشد -- پذیرفته نمی‌شود.
عبارت‌هایی مثل «رشد غیرعادی»، «رشد غیرعادی و نیازمند اقدام/بررسی»، «زیر حد پایین»،
«ریسک پایداری منابع»، «مورد نیازمند بررسی» و «نیازمند شفاف‌سازی» *وضعیت* نیستند؛
ارزیابی توصیفی‌اند و جای آن‌ها فیلدهای ``result``/``evidence``/``action`` است.
«قاعده ارزیابی» می‌گوید در چه شرایطی کدام وضعیت را انتخاب کنی، نه این‌که متن آن را
به‌عنوان وضعیت بنویسی (مثال: رشد بیش از سقف ۴۰٪ ⇒ «هشدار مدیریتی»).

# قاعده‌ی صدور هشدار قطعی

اگر معیاری آستانه‌ی مشخصی دارد، نتیجه را **قطعی و عددی** نسبت به همان آستانه بیان کن
(مثال: «سهم ثبت‌شده ۲۳٫۰۷ درصد است که ۶٫۹۳ واحد درصد کمتر از حداقل ۳۰ درصد تعیین‌شده است.»).
نتیجه‌ی قطعی عددی را به توصیه‌ی مبهم تبدیل نکن.

# محورها و معیارها (این فهرست قابل توسعه است و به شش محور محدود نیست)

محورهای زیر *همه* باید در ماتریس خطادهی ردیف داشته باشند؛ هیچ معیاری نباید به‌دلیل
خلاصه‌سازی از گزارش حذف شود. هر معیار یکی از چهار حالت «دارای انحراف / عادی / معاف /
فاقد داده کافی» را می‌گیرد. هر معیاری که روی چند ردیف اجرا می‌شود باید نتیجه‌ی ریزدانه‌ی
هر ردیف را جدا ثبت کند (اگر تعداد اقلام یک معیار از سقف تعیین‌شده بیشتر شد، اقلام
مهم‌تر را جدا و باقی را در یک ردیف تجمیعی با ذکر تعداد ثبت کن).

{criteria_prompt_block()}

# آستانه‌ها و پارامترهای فعال سامانه

{threshold_lines}

# اطلاعات اجباری هر مورد انحراف

محور، معیار، فرم، بخش، ردیف، ستون، سال پایه، سال جاری، مقدار پایه، مقدار جاری/واقعی،
حد مجاز، اختلاف مطلق، اختلاف درصدی، واحد، وضعیت، اهمیت، شواهد، پیام مدیریتی، ریسک و
اقدام پیشنهادی. هر موردی که وجود ندارد باید «{MISSING_TEXT}» بگیرد.

# ترتیب اولویت و ساختار گزارش

موارد را به ترتیب اهمیت (بسیار بالا ← بااهمیت ← قابل توجه ← عادی) و در داخل هر سطح به
ترتیب مبلغ مالی، درصد انحراف، اثر مدیریتی و ریسک مرتب کن. یک یافته‌ی کلی هرگز نباید باعث
حذف یافته‌های جزئی‌تر شود؛ هر مغایرت مهم یک رکورد مستقل می‌گیرد.

# کنترل کیفیت پیش از خروجی

واحد همه‌ی ارقام، سال مبنا و سال جاری، مخرج نسبت‌ها، علامت اعداد، تفکیک «صفر» از
«داده‌ی مفقود»، بازمحاسبه‌ی درصدها، مغایرت‌های بین‌فرمی، پرهیز از دوباره‌شماری یک مبلغ
و تطبیق جمع‌ها با اجزا را کنترل کن. اگر عددی در متن با جدول مغایرت داشت، جدول معتبرتر است.

# قالب خروجی

خروجی تو **فقط یک شیء JSON معتبر** است -- بدون هیچ متن، توضیح یا بلوک کد اضافه.
ساختار دقیقاً همان کلیدهای زیر است (مقادیر متنی فارسی):

{_OUTPUT_SCHEMA}

فیلدهای خالی را حذف نکن؛ اگر داده‌ای نیست همان متن «{MISSING_TEXT}» را بگذار.
در ``error_matrix`` دست‌کم یک ردیف برای **هر معیار** بگذار. ردیف‌های دارای انحراف/هشدار
باید حتماً ``form`` و یکی از ``row``/``column``/``evidence`` را داشته باشند؛ ردیف بدون
محل داده پذیرفته نمی‌شود."""


_OUTPUT_SCHEMA = """{
  "meta": {"organization": "...", "current_year": "...", "base_year": "...", "source_documents": ["..."]},
  "executive_summary": {"overall_status": "...", "top_deviation": "...", "top_financial_risk": "...",
                        "top_structural_risk": "...", "top_mission_issue": "...", "top_recommended_decision": "..."},
  "axis_dashboard": [{"axis": "...", "status": "...", "importance": "...",
                      "significant_findings_count": 0, "management_summary": "..."}],
  "error_matrix": [{"criterion_id": "C1.1", "axis": "...", "criterion": "...", "result": "...",
                    "status": "...", "importance": "...", "form": "...", "section": "...", "row": "...",
                    "column": "...", "base_value": 0, "current_value": 0, "threshold": 0,
                    "absolute_deviation": 0, "percentage_deviation": 0, "comparison_type": "...",
                    "base_document": "...", "comparison_document": "...", "base_year": "...",
                    "comparison_year": "...", "evidence": "...", "action": "..."}],
  "top_findings": [{"rank": 1, "axis": "...", "criterion": "...", "location": "...", "observed_value": 0,
                    "reference_value": 0, "deviation": 0, "deviation_unit": "واحد درصد | درصد | میلیون ریال",
                    "importance": "...", "management_message": "..."}],
  "significant_findings": [{"number": 1, "title": "...", "subject": "...",
                            "evidence": {"form": "...", "section": "...", "row": "...", "column": "...",
                                         "base_value": 0, "current_value": 0, "threshold": 0,
                                         "absolute_deviation": 0, "percentage_deviation": 0},
                            "assessment": "...", "management_importance": "...", "risk": "...", "action": "..."}],
  "risks": [{"description": "...", "approximate_amount": "قابل تعیین از اسناد موجود نیست",
             "importance": "...", "nature": "مالی | اجرایی | ساختاری | پایداری"}],
  "items_needing_decision": [{"subject": "...", "question": "...", "proposed_action": "..."}],
  "items_needing_clarification": [{"subject": "...", "available_data": "...",
                                   "missing_or_conflicting_data": "...", "location": "...",
                                   "reason": "...", "required_document": "..."}],
  "closing_notes": ["..."]
}"""


# ---------------------------------------------------------------------------
# رندر داده‌ی مرحله‌ی ۱
# ---------------------------------------------------------------------------
def _row_key(cell: ExtractedCell) -> tuple[str, Optional[int], str, Optional[str]]:
    return (cell.form_key, cell.row_number, cell.row_title, cell.section)


def _select_rows(
    cells: Sequence[ExtractedCell], max_rows: int
) -> tuple[list[tuple[str, Optional[int], str, Optional[str]]], int]:
    """ردیف‌های یک فرم را انتخاب می‌کند (سقف‌دار، با حفظ ردیف‌های جمع و مبالغ بزرگ)."""
    grouped: dict[tuple[str, Optional[int], str, Optional[str]], list[ExtractedCell]] = {}
    for cell in cells:
        grouped.setdefault(_row_key(cell), []).append(cell)

    keys = list(grouped)
    if len(keys) <= max_rows:
        return keys, 0

    def weight(key: tuple[str, Optional[int], str, Optional[str]]) -> float:
        values = [abs(cell.value) for cell in grouped[key]]
        return max(values) if values else 0.0

    # ردیف‌های «جمع» همیشه می‌مانند (لنگر عددی گزارش‌اند).
    totals = [key for key in keys if "جمع" in key[2] or "مجموع" in key[2]]
    remaining = [key for key in keys if key not in set(totals)]
    remaining.sort(key=weight, reverse=True)
    chosen = totals[: max_rows // 3] + remaining
    chosen = chosen[:max_rows]
    chosen.sort(key=lambda key: (key[1] is None, key[1] or 0, key[2]))
    return chosen, len(keys) - len(chosen)


@dataclass(frozen=True)
class RenderedDataBlock:
    """متن بخش داده‌ی پرامپت + نام سندهایی که به‌دلیل بودجه‌ی نویسه بریده شدند."""

    text: str
    truncated_documents: tuple[str, ...] = ()
    dropped_char_count: int = 0


def _truncate_at_line(text: str, limit: int) -> tuple[str, int]:
    """متن را از انتهای یک خط کامل می‌برد (نه از میان یک ردیف داده).

    بریدن از میان خط، ردیف/سلول را نیمه‌کاره به مدل می‌دهد و همان چیزی است که
    «محل داده» را نامعتبر می‌کند؛ به همین دلیل برش همیشه روی مرز خط انجام می‌شود.

    Returns:
        ``(متن بریده, تعداد نویسه‌های حذف‌شده)``
    """
    if len(text) <= limit:
        return text, 0
    if limit <= 0:
        return "", len(text)
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        # هیچ مرز خطی در بازه نبود (متن تک‌خطی) -- ناچار برش سخت.
        cut = limit
    return text[:cut], len(text) - cut


def _share_budget(lengths: Sequence[int], total: int, floor: int) -> list[int]:
    """سهم هر سند از بودجه‌ی نویسه‌ها.

    ابتدا هر سند یک کف (``floor``) می‌گیرد تا هیچ سندی -- مشخصاً سند سال جاری --
    به‌طور کامل از پرامپت حذف نشود (باکت‌های آب‌رسانی: هر سند کمتر از سهمش،
    سهم واقعی‌اش را می‌گیرد و باقی‌مانده بین بقیه تقسیم می‌شود).
    """
    count = len(lengths)
    if count == 0:
        return []
    if total <= 0:
        return [0] * count

    shares = [min(length, floor) for length in lengths]
    if sum(shares) > total:
        # بودجه حتی برای کف اولیه کافی نیست: تقسیم مساوی بدون کف.
        equal = total // count
        return [min(length, equal) for length in lengths]

    remaining = total - sum(shares)
    pending = {index for index, length in enumerate(lengths) if length > shares[index]}
    while pending and remaining > 0:
        share = remaining // len(pending)
        if share <= 0:
            break
        satisfied = {index for index in pending if lengths[index] - shares[index] <= share}
        if not satisfied:
            for index in pending:
                shares[index] += share
            break
        for index in satisfied:
            remaining -= lengths[index] - shares[index]
            shares[index] = lengths[index]
        pending -= satisfied
    return shares


def render_data_block(
    bundle: ExtractionBundle,
    *,
    max_rows_per_form: int = MAX_ROWS_PER_FORM,
    max_chars: Optional[int] = None,
) -> RenderedDataBlock:
    """نمای متنی هر دو سند با تقسیم عادلانه‌ی بودجه‌ی نویسه بین آن‌ها.

    هیچ‌گاه یک سند را برای سند دیگر کنار نمی‌گذارد: اگر حجم کل از سقف بگذرد،
    بودجه به نسبت بین اسناد تقسیم می‌شود و برش هر سند روی مرز خط انجام می‌گیرد.
    """
    budget = prompt_data_char_budget() if max_chars is None else max_chars
    blocks = [
        _render_document(document, bundle, max_rows_per_form=max_rows_per_form)
        for document in bundle.documents
    ]
    if not blocks:
        return RenderedDataBlock(text="")

    total = sum(len(block) for block in blocks)
    if total <= budget:
        return RenderedDataBlock(text="\n\n".join(blocks))

    shares = _share_budget([len(block) for block in blocks], budget, MIN_DOCUMENT_CHARS)
    kept: list[str] = []
    truncated: list[str] = []
    dropped = 0
    for index, block in enumerate(blocks):
        piece, removed = _truncate_at_line(block, shares[index])
        kept.append(piece)
        dropped += removed
        if removed:
            truncated.append(bundle.documents[index].filename)
    return RenderedDataBlock(
        text="\n\n".join(kept),
        truncated_documents=tuple(truncated),
        dropped_char_count=dropped,
    )


def render_documents_for_prompt(
    bundle: ExtractionBundle,
    *,
    max_rows_per_form: int = MAX_ROWS_PER_FORM,
    max_chars: Optional[int] = None,
) -> str:
    """نمای متنی هر دو سند برای پیام کاربر (ردیف‌محور و قابل ردیابی)."""
    return render_data_block(
        bundle, max_rows_per_form=max_rows_per_form, max_chars=max_chars
    ).text


def _render_document(
    document: DocumentExtraction, bundle: ExtractionBundle, *, max_rows_per_form: int
) -> str:
    lines: list[str] = []
    unit = document.unit or "نامشخص"
    lines.append(f"## سند «{document.filename}» -- {document.role_fa}")
    lines.append(
        "سال تشخیص‌داده‌شده: "
        + (document.detected_year or MISSING_TEXT)
        + f" | واحد: {unit} | روش استخراج: {document.extraction_method}"
        + (" | متن آینه‌ای PDF اصلاح شد" if document.mirrored_text_repaired else "")
    )
    if bundle.base_year or bundle.current_year:
        lines.append(
            f"سال پایه‌ی تحلیل: {bundle.base_year or MISSING_TEXT} | "
            f"سال جاری تحلیل: {bundle.current_year or MISSING_TEXT}"
        )

    if document.forms:
        lines.append("فرم‌های شناسایی‌شده:")
        for form in document.forms:
            lines.append(
                f"- {form.form_name} — شیت «{form.sheet_name}»، اطمینان {form.confidence:.2f}، "
                f"{form.cell_count} قلم عددی"
            )
    else:
        lines.append("فرم شناسایی‌شده‌ای وجود ندارد؛ این سند قابل تحلیل ابعادی نیست.")

    if document.missing_form_keys:
        lines.append(
            "فرم‌هایی که در این سند شناسایی نشدند (برای داده‌های مربوط به آن‌ها "
            f"«{MISSING_TEXT}» را درج کن): "
            + "، ".join(form_name(key) for key in document.missing_form_keys)
        )
    if document.unsupported_sheets:
        lines.append("شیت‌های شناسایی‌نشده (به هیچ فرمی نسبت داده نشدند): " + "، ".join(document.unsupported_sheets))

    if document.anchors:
        lines.append("")
        lines.append(
            "جمع‌های محاسبه‌شده‌ی سامانه از ردیف‌های «جمع» (مقادیر قطعی، قابل استناد):"
        )
        for key, value in list(document.anchors.items())[:80]:
            lines.append(f"- {key} = {value:,.0f}")

    for form in document.forms:
        cells = [cell for cell in document.cells if cell.form_key == form.form_key]
        if not cells:
            continue
        lines.append("")
        lines.append(f"### {form.form_name} — شیت «{form.sheet_name}»")
        lines.append("ستون‌ها: " + "، ".join(form.column_titles) if form.column_titles else "ستون‌ها: نامشخص")
        lines.append("ردیف‌ها (شکل: [ردیف] عنوان ردیف [بخش] :: ستون=مقدار (سال، محل)):")
        keys, dropped = _select_rows(cells, max_rows_per_form)
        grouped: dict[tuple[str, Optional[int], str, Optional[str]], list[ExtractedCell]] = {}
        for cell in cells:
            grouped.setdefault(_row_key(cell), []).append(cell)
        for key in keys:
            form_key, row_number, row_title, section = key
            values = grouped[key]
            rendered = " | ".join(
                f"{cell.column_title}={plain_number(cell.value)}"
                f" (سال {cell.year or MISSING_TEXT}"
                + (f"، محل {cell.cell_ref}" if cell.cell_ref else "")
                + ")"
                for cell in values
            )
            number_part = f"[ردیف {row_number}] " if row_number is not None else ""
            section_part = f" [بخش: {section}]" if section else ""
            lines.append(f"- {number_part}{row_title}{section_part} :: {rendered}")
        if dropped:
            lines.append(
                f"  (توجه: {dropped} ردیف کم‌اهمیت‌تر این فرم به‌دلیل حجم داده در این فهرست نیامده "
                "است؛ نبود آن‌ها به‌معنای نبود داده در سند نیست.)"
            )

    for warning in document.warnings:
        lines.append(f"⚠ هشدار استخراج: {warning}")
    return "\n".join(lines)


def build_user_prompt(
    bundle: ExtractionBundle,
    config: BudgetConfig,
    *,
    organization: Optional[str] = None,
    meeting_context: Optional[str] = None,
    extra_note: Optional[str] = None,
) -> str:
    """پیام ``user``: داده‌ی ساختارمند + درخواست خروجی JSON."""
    data = render_data_block(bundle)

    header_lines = [
        "# درخواست",
        "اسناد بودجه‌ی زیر را طبق قواعد پیام system تحلیل کن و خروجی را فقط به‌صورت یک شیء JSON "
        "با همان ساختار تعیین‌شده برگردان.",
        "",
        "نام سازمان: " + (organization.strip() if organization and organization.strip() else MISSING_TEXT),
    ]
    if meeting_context and meeting_context.strip():
        header_lines.append("توضیح جلسه: " + meeting_context.strip())
    header_lines.append(f"سال پایه: {bundle.base_year or MISSING_TEXT} | سال جاری: {bundle.current_year or MISSING_TEXT}")
    header_lines.append(
        f"سقف ثبت موارد جزئی: حداکثر {config.max_detail_findings_per_axis} ردیف به‌ازای هر محور و "
        f"حداکثر {config.max_top_findings} ردیف در جدول مهم‌ترین انحرافات."
    )
    if bundle.warnings:
        header_lines.append("")
        header_lines.append("هشدارهای مرحله‌ی استخراج (در گزارش لحاظ کن):")
        header_lines.extend(f"- {warning}" for warning in bundle.warnings)

    if extra_note:
        header_lines.extend(["", extra_note])

    if data.truncated_documents:
        header_lines.extend(
            [
                "",
                "توجه: فهرست اقلام سند(های) زیر به‌دلیل حجم داده در این پیام بریده شده است: "
                + "، ".join(f"«{name}»" for name in data.truncated_documents)
                + ". نبودن یک ردیف در این فهرست به‌معنای نبود آن در سند نیست و نباید "
                "به‌عنوان «" + MISSING_TEXT + "» گزارش شود. برای معیارهایی که به داده‌ی "
                "بریده‌شده نیاز دارند وضعیت «فاقد داده کافی» بگذار، نه انحراف.",
            ]
        )

    return "\n".join(header_lines) + "\n\n# داده‌ی استخراج‌شده از اسناد\n\n" + data.text


def build_messages(
    bundle: ExtractionBundle,
    config: BudgetConfig,
    *,
    organization: Optional[str] = None,
    meeting_context: Optional[str] = None,
    extra_note: Optional[str] = None,
) -> list[dict[str, str]]:
    """پیام‌های آماده‌ی ارسال به مدل (system + user)."""
    return [
        {"role": "system", "content": build_system_prompt(config)},
        {
            "role": "user",
            "content": build_user_prompt(
                bundle,
                config,
                organization=organization,
                meeting_context=meeting_context,
                extra_note=extra_note,
            ),
        },
    ]
