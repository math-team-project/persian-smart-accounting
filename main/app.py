"""
داشبورد هوشمند حسابرسی و حسابداری (Persian Smart Accounting)
================================================================
اپلیکیشن اصلی داشبورد که با Streamlit ساخته شده و شامل دو کارگاه (workspace)
مستقل از هم است که از طریق نوار کناری قابل‌سوییچ هستند:

    ۱) «چک‌لیست حسابرسی مالی»: گردش‌کاری main/main.ipynb را روی فایل‌های اکسل
       آپلودشده اجرا می‌کند (استخراج بودجه/صورت‌های مالی/ترازنامه، اجرای کامل
       چک‌لیست حسابرسی طبق extraction_script/scripts/checklist و نمایش نتایج).
    ۲) «خلاصه‌سازی گزارش حسابرسی»: یک گزارش حسابرسی متنی (PDF/DOC/DOCX) را با
       کمک هوش مصنوعی (بسته‌ی audit_summarizer) به خلاصه‌ای روان و آماده‌ی
       جلسه تبدیل می‌کند. این پردازش (تماس با مدل زبانی) ممکن است طول بکشد،
       به همین دلیل در یک ترد پس‌زمینه اجرا می‌شود و نتیجه هر زمان آماده شد،
       بدون مسدودکردن کارگاه دیگر، به‌صورت خودکار نمایش داده می‌شود.

اجرا:
    streamlit run main/app.py
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_pipeline import (  # noqa: E402
    make_audit_temp_workdir,
    new_audit_job,
    save_audit_upload,
    start_audit_summary_job,
)
from icons import icon  # noqa: E402
from pipeline import (  # noqa: E402
    FILE_SLOTS,
    STATUS_META,
    PipelineError,
    cleanup_workdir,
    flatten_sheets_for_preview,
    make_temp_workdir,
    run_full_pipeline,
    save_uploaded_file,
)
from styles import (  # noqa: E402
    ACCENT,
    ACCENT_SKY,
    BG_ELEVATED,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    inject_custom_css,
)

st.set_page_config(
    page_title="داشبورد هوشمند حسابرسی و حسابداری",
    page_icon=":material/fact_check:",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_custom_css(st)

if "result" not in st.session_state:
    st.session_state["result"] = None
if "audit_job" not in st.session_state:
    st.session_state["audit_job"] = new_audit_job()

# نگاشت وضعیت سوالات چک‌لیست به آیکون Material برای عنوان expander (که HTML خام نمی‌پذیرد)
EXPANDER_ICON = {
    "TRUE": "check_circle",
    "FALSE": "cancel",
    "ERROR": "warning",
    "MANUAL": "visibility",
}
BREAKDOWN_LABEL = {True: "درست", False: "نادرست"}

PAGE_LABELS = {
    "checklist": "چک‌لیست حسابرسی مالی",
    "audit_summary": "خلاصه‌سازی گزارش حسابرسی",
}


def render_alert(icon_name: str, kind: str, message: str) -> None:
    """جعبه‌ی اعلان سفارشی با آیکون SVG به‌جای ایموجی/پونز."""
    st.markdown(
        f"""
        <div class="psa-alert {kind}">
            {icon(icon_name, 22)}
            <div class="psa-alert-text">{message}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def style_plotly(fig: go.Figure, height: int | None = None) -> go.Figure:
    """اعمال تم تیره‌ی یکنواخت روی نمودارهای Plotly."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT_PRIMARY, family="Vazirmatn, Tahoma, sans-serif"),
        legend=dict(font=dict(color=TEXT_SECONDARY)),
    )
    if height:
        fig.update_layout(height=height)
    return fig


def _render_audit_logs(job: dict, *, expanded: bool) -> None:
    """نمایش گزارش (لاگ) مراحل پردازش خلاصه‌سازی، دقیقاً معادل پیام‌هایی که
    نسخه‌ی خط‌فرمان audit_summarizer/main.py با print() چاپ می‌کند.

    خطوط بیش‌ازحد طولانی (مثل JSON خام پاسخ مدل) از قبل در audit_pipeline.py
    کوتاه شده‌اند تا لاگ شلوغ نشود.
    """
    logs = job.get("logs") or []
    if not logs:
        return
    with st.expander(f"گزارش پردازش ({len(logs)} خط)", expanded=expanded):
        st.code("\n".join(logs), language="text")


@st.fragment(run_every="2s")
def render_audit_summary_status(job: dict) -> None:
    """نمایش زنده‌ی وضعیت خلاصه‌سازی گزارش حسابرسی (اجرا در ترد پس‌زمینه).

    این تابع به‌عنوان یک st.fragment تعریف شده تا هر ۲ ثانیه، فقط همین بخش از
    صفحه (بدون رفرش کل داشبورد) بازخوانی شود و وضعیت ترد پس‌زمینه را نمایش دهد.
    """
    status = job["status"]

    if status == "idle":
        render_alert(
            "info", "info",
            "برای شروع، یک فایل گزارش حسابرسی بارگذاری و دکمه «شروع خلاصه‌سازی» را بزنید.",
        )
        return

    if status == "running":
        elapsed = time.time() - (job.get("started_at") or time.time())
        st.markdown(
            f"""
            <div class="psa-alert info">
                {icon('clock', 22)}
                <div class="psa-alert-text">
                    در حال خلاصه‌سازی «{job.get('source_filename', '')}» با هوش مصنوعی...
                    ({elapsed:.0f} ثانیه) &nbsp;
                    <span class="psa-badge processing">{icon('sparkles', 11)}در حال پردازش</span>
                    <br/>
                    می‌توانید همزمان کارگاه چک‌لیست حسابرسی مالی را نیز پردازش کنید؛ نتیجه‌ی این
                    خلاصه به‌محض آماده‌شدن، به‌طور خودکار همین‌جا نمایش داده می‌شود.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_audit_logs(job, expanded=True)
        return

    if status == "error":
        render_alert("alert-triangle", "error", f"خلاصه‌سازی ناموفق بود: {job.get('error')}")
        _render_audit_logs(job, expanded=True)
        return

    if status == "done":
        result = job["result"]
        st.markdown(
            f'<span class="psa-badge done">{icon("check-circle", 11)}خلاصه آماده شد '
            f'({result["elapsed_seconds"]:.0f} ثانیه)</span>',
            unsafe_allow_html=True,
        )
        for w in result.get("warnings", []):
            render_alert("alert-triangle", "warning", w)

        st.download_button(
            "دانلود خلاصه گزارش حسابرسی (Word)",
            data=result["docx_bytes"],
            file_name=result["docx_filename"],
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            icon=":material/download:",
            width="stretch",
        )
        with st.expander("پیش‌نمایش متن خلاصه", expanded=False):
            st.markdown(result["summary_markdown"])
        _render_audit_logs(job, expanded=False)


# ---------------------------------------------------------------------------
# نوار کناری
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        f"""
        <div class="psa-sidebar-brand">
            <div class="psa-icon-badge">{icon('shield-check', 20)}</div>
            <div>
                <div class="psa-sidebar-title">داشبورد هوشمند حسابرسی</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()

    st.markdown('<div class="psa-section-title" style="font-size:.92rem;">کارگاه</div>', unsafe_allow_html=True)
    active_page = st.segmented_control(
        "انتخاب کارگاه",
        options=list(PAGE_LABELS.keys()),
        format_func=lambda k: PAGE_LABELS[k],
        default="checklist",
        key="active_page",
        label_visibility="collapsed",
        width="stretch",
    )
    if not active_page:
        active_page = "checklist"

    st.divider()
    st.markdown('<div class="psa-section-title" style="font-size:.92rem;">راهنمای سریع</div>', unsafe_allow_html=True)

    if active_page == "checklist":
        steps = [
            "فایل‌های الزامی (بودجه اصلاحیه، صورت‌های مالی و ترازنامه) را بارگذاری کنید.",
            "در صورت وجود، فایل‌های تاییدیه اعتبارات و قانون بودجه را نیز اضافه کنید.",
            "روی دکمه «پردازش و تحلیل داده‌ها» بزنید.",
            "نتایج را در تب‌های نمای کلی، چک‌لیست، داده‌ها و خروجی گزارش ببینید.",
        ]
    else:
        steps = [
            "فایل گزارش حسابرسی (PDF، DOC یا DOCX) را بارگذاری کنید.",
            "روی دکمه «شروع خلاصه‌سازی با هوش مصنوعی» بزنید.",
            "پردازش در پس‌زمینه انجام می‌شود؛ می‌توانید همزمان کارگاه دیگر را نیز استفاده کنید.",
            "گزارش پردازش (لاگ) و خلاصه‌ی نهایی هر زمان آماده شد، همین‌جا نمایش داده می‌شود.",
        ]
    steps_html = "".join(
        f"""<li class="psa-step">
                <div class="psa-step-num">{i}</div>
                <div class="psa-step-text">{text}</div>
            </li>"""
        for i, text in enumerate(steps, start=1)
    )
    st.markdown(f"<ul>{steps_html}</ul>", unsafe_allow_html=True)

    st.divider()

    if active_page == "checklist":
        if st.session_state.pop("show_success", False):
            st.success(
                f"پردازش با موفقیت انجام شد "
                f"({st.session_state['result']['elapsed_seconds']:.1f} ثانیه).",
                icon=":material/task_alt:",
            )

        if st.session_state["result"] is not None:
            if st.button(
                "پاک‌کردن نتایج و شروع مجدد",
                icon=":material/restart_alt:",
                width="stretch",
                key="clear_checklist_results",
            ):
                st.session_state["result"] = None
                for key in FILE_SLOTS:
                    st.session_state.pop(f"upload_{key}", None)
                st.rerun()
            st.divider()
    else:
        audit_job_sidebar = st.session_state["audit_job"]
        if audit_job_sidebar["status"] in ("done", "error"):
            if st.button(
                "پاک‌کردن نتیجه‌ی خلاصه‌سازی",
                icon=":material/restart_alt:",
                width="stretch",
                key="clear_audit_job",
            ):
                st.session_state["audit_job"] = new_audit_job()
                st.session_state.pop("upload_audit_report", None)
                st.rerun()
            st.divider()

    st.caption("پروژه کارشناسی \n استاد محترم: دکتر قنبری \n دانشجویان: حامد حسامی، آرمین خوجوی")


def render_checklist_page() -> None:
    """کارگاه ۱: بارگذاری فایل‌های اکسل و اجرای کامل چک‌لیست حسابرسی مالی."""
    st.markdown(
        f"""
        <div class="psa-hero">
            <div class="psa-hero-top">
                <div class="psa-hero-badge">{icon('shield-check', 26)}</div>
                <h1>داشبورد هوشمند حسابرسی</h1>
            </div>
            <p class="psa-hero-desc">
                فایل‌های بودجه، صورت‌های مالی و ترازنامه را بارگذاری کنید تا چک‌لیست حسابرسی به‌صورت
                خودکار اجرا شود؛ مغایرت‌ها کشف، تطابق ارقام پیگیری و گزارش نهایی تولید می‌شود — همه در یک نگاه.
            </p>
            <div class="psa-badges">
                <span class="psa-chip">{icon('sparkles', 15)} تحلیل خودکار</span>
                <span class="psa-chip">{icon('list-checks', 15)} چک‌لیست هوشمند حسابرسی</span>
                <span class="psa-chip">{icon('file-down', 15)} خروجی اکسل / JSON</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- بخش بارگذاری فایل‌ها ------------------------------------------------
    st.markdown(
        f'<div class="psa-section-title">{icon("upload-cloud", 20)} بارگذاری فایل‌های ورودی</div>',
        unsafe_allow_html=True,
    )

    required_keys = [k for k, v in FILE_SLOTS.items() if v["required"]]
    optional_keys = [k for k, v in FILE_SLOTS.items() if not v["required"]]

    uploaded_files: dict[str, object] = {}

    def _render_upload_card(col, key: str) -> None:
        slot = FILE_SLOTS[key]
        is_required = slot["required"]
        badge_class = "required" if is_required else "optional"
        badge_text = "الزامی" if is_required else "اختیاری"
        badge_icon = "asterisk" if is_required else "circle"

        current_value = st.session_state.get(f"upload_{key}")
        status_class = "uploaded" if current_value is not None else "empty"
        status_text = "بارگذاری شد" if current_value is not None else "خالی"
        status_icon = "check-circle" if current_value is not None else "circle"

        with col:
            with st.container(border=True):
                st.markdown(
                    f"""
                    <div class="psa-upload-marker"></div>
                    <div class="psa-upload-head">
                        <div class="psa-upload-title">{icon(slot['icon'], 18)}<span>{slot['label']}</span></div>
                        <span class="psa-badge {badge_class}">{icon(badge_icon, 11)}{badge_text}</span>
                    </div>
                    <p class="psa-upload-help">{slot['help']}</p>
                    <span class="psa-badge {status_class}" style="margin-bottom:.5rem;">
                        {icon(status_icon, 11)}{status_text}
                    </span>
                    """,
                    unsafe_allow_html=True,
                )
                uploaded_files[key] = st.file_uploader(
                    slot["label"],
                    type=["xlsx", "xls", "pdf"],
                    key=f"upload_{key}",
                    label_visibility="collapsed",
                )

    all_keys = required_keys + optional_keys

    for i in range(0, len(all_keys), 2):
        row_cols = st.columns(2, gap="medium")
        for col, key in zip(row_cols, all_keys[i:i + 2]):
            _render_upload_card(col, key)

    st.write("")
    process_clicked = st.button(
        "پردازش و تحلیل داده‌ها",
        icon=":material/bolt:",
        type="primary",
        width="stretch",
    )

    if process_clicked:
        missing_labels = [FILE_SLOTS[k]["label"] for k in required_keys if uploaded_files.get(k) is None]
        if missing_labels:
            st.error("لطفاً فایل‌های الزامی زیر را بارگذاری کنید: " + "، ".join(missing_labels), icon=":material/error:")
        else:
            workdir = make_temp_workdir()
            try:
                with st.spinner("در حال استخراج داده‌ها و اجرای چک‌لیست حسابرسی... این عملیات ممکن است چند ثانیه طول بکشد."):
                    file_paths = {}
                    for key, uploaded in uploaded_files.items():
                        file_paths[key] = save_uploaded_file(uploaded, workdir) if uploaded is not None else None
                    result = run_full_pipeline(file_paths)
                st.session_state["result"] = result
                st.session_state["show_success"] = True
                # اجرای مجدد برنامه تا Sidebar با نتیجه جدید رندر شود
                st.rerun()
            except PipelineError as exc:
                st.error(str(exc), icon=":material/error:")
            except Exception as exc:  # noqa: BLE001
                st.error("خطای غیرمنتظره‌ای هنگام پردازش رخ داد.", icon=":material/error:")
                with st.expander("جزئیات فنی خطا"):
                    st.exception(exc)
            finally:
                cleanup_workdir(workdir)

    # --- داشبورد نتایج -------------------------------------------------------
    result = st.session_state["result"]

    if result is None:
        render_alert(
            "info",
            "info",
            "برای مشاهده داشبورد، ابتدا فایل‌های الزامی را بارگذاری و دکمه «پردازش و تحلیل داده‌ها» را بزنید.",
        )
        return

    for warning in result.get("warnings", []):
        render_alert("alert-triangle", "warning", warning)

    summary = result["summary"]
    checklist_results = result["checklist_results"]

    tab_overview, tab_checklist, tab_data, tab_export = st.tabs(
        [
            ":material/insights: نمای کلی",
            ":material/fact_check: چک‌لیست حسابرسی",
            ":material/database: داده‌های استخراج‌شده",
            ":material/file_download: خروجی گزارش",
        ]
    )

    # --- تب نمای کلی -----------------------------------------------------------
    with tab_overview:
        st.markdown(
            f'<div class="psa-section-title">{icon("layout-dashboard", 20)} شاخص‌های کلیدی عملکرد</div>',
            unsafe_allow_html=True,
        )
        kpi_cols = st.columns(6)
        kpi_data = [
            ("total", "list-checks", "کل سوالات چک‌لیست", summary["total"]),
            ("pass", "check-circle", "تطابق دارد", summary["true_count"]),
            ("fail", "x-circle", "عدم تطابق", summary["false_count"]),
            ("error", "alert-triangle", "خطای پردازش", summary["error_count"]),
            ("manual", "eye", "نیازمند بررسی دستی", summary["manual_count"]),
            ("rate", "gauge", "درصد تطابق", f"{summary['compliance_rate']:.1f}٪"),
        ]
        for col, (variant, icon_name, label, value) in zip(kpi_cols, kpi_data):
            with col:
                st.markdown(
                    f"""
                    <div class="psa-kpi {variant}">
                        <div class="psa-kpi-top">{icon(icon_name, 20)}</div>
                        <div class="psa-kpi-value">{value}</div>
                        <div class="psa-kpi-label">{label}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        st.write("")
        chart_col1, chart_col2 = st.columns([1.1, 1])

        with chart_col1:
            st.markdown(
                f'<div class="psa-section-title">{icon("pie-chart", 18)} توزیع وضعیت سوالات چک‌لیست</div>',
                unsafe_allow_html=True,
            )
            pie_labels = [STATUS_META[s]["label"] for s in ["TRUE", "FALSE", "ERROR", "MANUAL"]]
            pie_values = [summary["true_count"], summary["false_count"], summary["error_count"], summary["manual_count"]]
            pie_colors = [STATUS_META[s]["color"] for s in ["TRUE", "FALSE", "ERROR", "MANUAL"]]
            fig_pie = go.Figure(
                data=[
                    go.Pie(
                        labels=pie_labels,
                        values=pie_values,
                        hole=0.58,
                        marker=dict(colors=pie_colors, line=dict(color=BG_ELEVATED, width=2)),
                        textinfo="label+value",
                        textfont=dict(color=TEXT_PRIMARY),
                        sort=False,
                    )
                ]
            )
            fig_pie.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=True,
                legend=dict(orientation="h", y=-0.15),
                annotations=[
                    dict(
                        text=f"{summary['total']}<br>سوال",
                        x=0.5,
                        y=0.5,
                        font_size=16,
                        font_color=TEXT_PRIMARY,
                        showarrow=False,
                    )
                ],
            )
            st.plotly_chart(style_plotly(fig_pie), width="stretch")

        with chart_col2:
            st.markdown(
                f'<div class="psa-section-title">{icon("gauge", 18)} نرخ تطابق کلی</div>',
                unsafe_allow_html=True,
            )
            fig_gauge = go.Figure(
                go.Indicator(
                    mode="gauge+number",
                    value=summary["compliance_rate"],
                    number={"suffix": "٪", "font": {"color": TEXT_PRIMARY}},
                    gauge={
                        "axis": {"range": [0, 100], "tickcolor": TEXT_SECONDARY},
                        "bar": {"color": ACCENT_SKY},
                        "bgcolor": BG_ELEVATED,
                        "bordercolor": BORDER,
                        "steps": [
                            {"range": [0, 50], "color": "rgba(239, 68, 68, 0.25)"},
                            {"range": [50, 80], "color": "rgba(245, 158, 11, 0.25)"},
                            {"range": [80, 100], "color": "rgba(16, 185, 129, 0.25)"},
                        ],
                    },
                )
            )
            fig_gauge.update_layout(margin=dict(t=25, b=10, l=25, r=25), height=280)
            st.plotly_chart(style_plotly(fig_gauge), width="stretch")

        st.markdown(
            f'<div class="psa-section-title">{icon("bar-chart", 18)} شیت‌های استخراج‌شده به تفکیک فایل ورودی</div>',
            unsafe_allow_html=True,
        )
        source_counts = result.get("sheet_source_counts", {})
        if source_counts:
            palette = [ACCENT, ACCENT_SKY, "#8B5CF6", "#F59E0B", "#EF4444"]
            fig_bar = go.Figure(
                go.Bar(
                    x=list(source_counts.values()),
                    y=list(source_counts.keys()),
                    orientation="h",
                    text=list(source_counts.values()),
                    textposition="outside",
                    marker=dict(color=(palette * 3)[: len(source_counts)]),
                )
            )
            fig_bar.update_layout(
                showlegend=False,
                xaxis_title="تعداد شیت استخراج‌شده",
                yaxis_title="",
                margin=dict(t=10, b=10, l=10, r=10),
                xaxis=dict(gridcolor=BORDER, color=TEXT_SECONDARY),
                yaxis=dict(color=TEXT_PRIMARY),
            )
            st.plotly_chart(style_plotly(fig_bar), width="stretch")
        st.caption(f"مجموع شیت‌های بارگذاری‌شده در حافظه: {len(result['imported_sheets'])}")

    # --- تب چک‌لیست حسابرسی -----------------------------------------------------
    with tab_checklist:
        st.markdown(
            f'<div class="psa-section-title">{icon("list-checks", 20)} فهرست کامل سوالات چک‌لیست حسابرسی</div>',
            unsafe_allow_html=True,
        )

        filter_col1, filter_col2 = st.columns([2, 1])
        with filter_col1:
            search_term = st.text_input(":material/search: جستجو در متن سوال / هدف بررسی", "")
        with filter_col2:
            status_options = ["همه"] + [STATUS_META[s]["label"] for s in ["TRUE", "FALSE", "ERROR", "MANUAL"]]
            status_filter = st.selectbox(":material/filter_list: وضعیت", status_options)

        label_to_status = {v["label"]: k for k, v in STATUS_META.items()}

        filtered_results = []
        for record in checklist_results:
            if status_filter != "همه" and record["status"] != label_to_status[status_filter]:
                continue
            if search_term:
                haystack = " ".join(
                    [record["question_text"], record["question_purpose"], record["general_description"]]
                ).lower()
                if search_term.lower() not in haystack:
                    continue
            filtered_results.append(record)

        st.caption(f"نمایش {len(filtered_results)} سوال از مجموع {len(checklist_results)} سوال چک‌لیست")

        for record in filtered_results:
            meta = STATUS_META[record["status"]]
            short_question = (record["question_text"] or "بدون متن").strip()
            if len(short_question) > 80:
                short_question = short_question[:80] + "…"
            header = f":material/{EXPANDER_ICON[record['status']]}: {record['question_id']} — {short_question}"

            with st.expander(header):
                st.markdown(
                    f"<span class='psa-status-badge' style='background:{meta['color']}'>"
                    f"{icon(meta['icon'], 14)}{meta['label']}</span>",
                    unsafe_allow_html=True,
                )
                st.write("")
                st.markdown(
                    f"<div style='margin-bottom:.5rem;'>{icon('file-text', 15)} "
                    f"<b>متن سوال:</b> {record['question_text'] or '—'}</div>",
                    unsafe_allow_html=True,
                )
                if record["question_purpose"]:
                    st.markdown(
                        f"<div style='margin-bottom:.5rem;'>{icon('target', 15)} "
                        f"<b>هدف بررسی:</b> {record['question_purpose']}</div>",
                        unsafe_allow_html=True,
                    )
                if record["general_description"]:
                    st.markdown(
                        f"<div style='margin-bottom:.5rem;'>{icon('info', 15)} "
                        f"<b>توضیحات تکمیلی:</b> {record['general_description']}</div>",
                        unsafe_allow_html=True,
                    )

                if record["evaluation_condition"]:
                    st.markdown(
                        f"<div style='margin:.7rem 0 .3rem 0;'>{icon('settings', 15)} <b>فرمول ارزیابی:</b></div>",
                        unsafe_allow_html=True,
                    )
                    st.code(record["evaluation_condition"], language="text")

                if record["condition_breakdown"]:
                    st.markdown(
                        f"<div style='margin:.7rem 0 .3rem 0;'>{icon('list-checks', 15)} <b>ریز ارزیابی شرط‌ها:</b></div>",
                        unsafe_allow_html=True,
                    )
                    breakdown_rows = []
                    for item in record["condition_breakdown"]:
                        res = item["result"]
                        label = BREAKDOWN_LABEL.get(res, "نامشخص")
                        breakdown_rows.append({"شرط": item["condition"], "نتیجه": label})
                    st.dataframe(pd.DataFrame(breakdown_rows), width="stretch", hide_index=True)

                if record["extracted_data"]:
                    st.markdown(
                        f"<div style='margin:.7rem 0 .3rem 0;'>{icon('database', 15)} <b>داده‌های استخراج‌شده:</b></div>",
                        unsafe_allow_html=True,
                    )
                    st.dataframe(pd.DataFrame(record["extracted_data"]), width="stretch", hide_index=True)

                if record["message"]:
                    final_text = f"نتیجه نهایی: {record['message']}"
                    if record["status"] == "TRUE":
                        st.success(final_text, icon=":material/check_circle:")
                    elif record["status"] == "FALSE":
                        st.error(final_text, icon=":material/cancel:")
                    elif record["status"] == "ERROR":
                        st.warning(final_text, icon=":material/warning:")
                    else:
                        st.info(final_text, icon=":material/visibility:")

    # --- تب داده‌های استخراج‌شده -------------------------------------------------
    with tab_data:
        st.markdown(
            f'<div class="psa-section-title">{icon("database", 20)} مرور شیت‌های استخراج‌شده</div>',
            unsafe_allow_html=True,
        )
        flat_sheets = flatten_sheets_for_preview(result["imported_sheets"])
        if not flat_sheets:
            render_alert("info", "info", "هیچ داده‌ای برای نمایش وجود ندارد.")
        else:
            sheet_choice = st.selectbox(":material/table_view: انتخاب شیت برای پیش‌نمایش", sorted(flat_sheets.keys()))
            payload = flat_sheets[sheet_choice]
            if isinstance(payload, pd.DataFrame):
                st.dataframe(payload, width="stretch")
                st.caption(f"ابعاد داده: {payload.shape[0]} سطر × {payload.shape[1]} ستون")
            else:
                st.write(payload)

    # --- تب خروجی گزارش ---------------------------------------------------------
    with tab_export:
        st.markdown(
            f'<div class="psa-section-title">{icon("file-down", 20)} دانلود گزارش نهایی</div>',
            unsafe_allow_html=True,
        )
        st.caption("خروجی شامل خلاصه شاخص‌ها و جزئیات کامل تمام سوالات چک‌لیست حسابرسی است.")

        export_rows = []
        for record in checklist_results:
            export_rows.append(
                {
                    "شناسه سوال": record["question_id"],
                    "متن سوال": record["question_text"],
                    "وضعیت": STATUS_META[record["status"]]["label"],
                    "هدف بررسی": record["question_purpose"],
                    "توضیحات تکمیلی": record["general_description"],
                    "فرمول ارزیابی": record["evaluation_condition"] or "",
                    "نتیجه نهایی": record["message"],
                }
            )
        export_df = pd.DataFrame(export_rows)
        summary_df = pd.DataFrame(
            [
                {
                    "کل سوالات": summary["total"],
                    "تطابق دارد": summary["true_count"],
                    "عدم تطابق": summary["false_count"],
                    "خطای پردازش": summary["error_count"],
                    "نیازمند بررسی دستی": summary["manual_count"],
                    "درصد تطابق": f"{summary['compliance_rate']:.1f}٪",
                }
            ]
        )

        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            summary_df.to_excel(writer, sheet_name="خلاصه", index=False)
            export_df.to_excel(writer, sheet_name="چک لیست حسابرسی", index=False)

        dl_col1, dl_col2 = st.columns(2)
        with dl_col1:
            st.download_button(
                "دانلود گزارش اکسل",
                data=excel_buffer.getvalue(),
                file_name="گزارش_حسابرسی.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                width="stretch",
            )
        with dl_col2:
            json_payload = json.dumps(
                {"summary": summary, "checklist": checklist_results}, ensure_ascii=False, indent=2
            ).encode("utf-8")
            st.download_button(
                "دانلود گزارش JSON",
                data=json_payload,
                file_name="گزارش_حسابرسی.json",
                mime="application/json",
                icon=":material/download:",
                width="stretch",
            )

        st.markdown(
            f"<div style='margin-top:1rem;'>{icon('list-checks', 15)} <b>پیش‌نمایش جدول چک‌لیست:</b></div>",
            unsafe_allow_html=True,
        )
        st.dataframe(export_df, width="stretch", hide_index=True)


def render_audit_summary_page() -> None:
    """کارگاه ۲: خلاصه‌سازی هوشمند گزارش حسابرسی (پردازش پس‌زمینه، غیرمسدودکننده)."""
    st.markdown(
        f"""
        <div class="psa-hero">
            <div class="psa-hero-top">
                <div class="psa-hero-badge">{icon('sparkles', 26)}</div>
                <h1>خلاصه‌سازی هوشمند گزارش حسابرسی</h1>
            </div>
            <p class="psa-hero-desc">
                گزارش حسابرسی (PDF یا Word) را بارگذاری کنید تا با کمک هوش مصنوعی به خلاصه‌ای روان و
                آماده‌ی جلسه تبدیل شود. این پردازش (تماس با مدل زبانی) ممکن است چند دقیقه طول بکشد و
                به‌صورت کاملاً مستقل از کارگاه چک‌لیست حسابرسی مالی، در پس‌زمینه اجرا می‌شود.
            </p>
            <div class="psa-badges">
                <span class="psa-chip">{icon('sparkles', 15)} خلاصه‌سازی با هوش مصنوعی</span>
                <span class="psa-chip">{icon('file-text', 15)} PDF / DOC / DOCX</span>
                <span class="psa-chip">{icon('file-down', 15)} خروجی Word</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    audit_job = st.session_state["audit_job"]

    with st.container(border=True):
        st.markdown(
            f"""
            <div class="psa-upload-marker"></div>
            <div class="psa-upload-head">
                <div class="psa-upload-title">{icon('file-text', 18)}<span>گزارش حسابرسی</span></div>
                <span class="psa-badge optional">{icon('circle', 11)}اختیاری</span>
            </div>
            <p class="psa-upload-help">فرمت‌های مجاز: PDF، DOC، DOCX</p>
            """,
            unsafe_allow_html=True,
        )
        audit_uploaded = st.file_uploader(
            "گزارش حسابرسی",
            type=["pdf", "doc", "docx"],
            key="upload_audit_report",
            label_visibility="collapsed",
            disabled=audit_job["status"] == "running",
        )

        action_col, clear_col = st.columns([3, 1])
        with action_col:
            audit_start_clicked = st.button(
                "شروع خلاصه‌سازی با هوش مصنوعی",
                icon=":material/auto_awesome:",
                type="primary",
                width="stretch",
                disabled=audit_uploaded is None or audit_job["status"] == "running",
            )
        with clear_col:
            audit_clear_clicked = audit_job["status"] in ("done", "error") and st.button(
                "پاک‌کردن", icon=":material/restart_alt:", width="stretch", key="clear_audit_main"
            )

        if audit_clear_clicked:
            st.session_state["audit_job"] = new_audit_job()
            st.session_state.pop("upload_audit_report", None)
            st.rerun()

        if audit_start_clicked and audit_uploaded is not None:
            audit_workdir = make_audit_temp_workdir()
            audit_saved_path = save_audit_upload(audit_uploaded, audit_workdir)
            start_audit_summary_job(audit_job, audit_saved_path, audit_workdir)
            st.rerun()

        render_audit_summary_status(audit_job)


# ---------------------------------------------------------------------------
# رندر کارگاه فعال
# ---------------------------------------------------------------------------
if active_page == "audit_summary":
    render_audit_summary_page()
else:
    render_checklist_page()

st.markdown(
    f"""
    <div class="psa-footer">
        {icon('shield-check', 14)}
        داشبورد هوشمند حسابرسی فارسی —
        پروژه کارشناسی رشته ریاضیات و کاربردات، دانشگاه فردوسی مشهد
    </div>
    """,
    unsafe_allow_html=True,
)
