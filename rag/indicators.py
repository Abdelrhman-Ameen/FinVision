"""Route supported historical figures to dated observations rather than prose search."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re

from .data_tools.rag_library import connect_readonly, lookup_indicator, searchable


METRICS = (
    ("FP.CPI.TOTL.ZG", ("تضخم", "inflation", "cpi"), "التضخم السنوي لأسعار المستهلك",
     "eg-cbe-inflation"),
    ("PA.NUS.FCRF", ("دولار", "سعر الصرف", "exchange rate", "usd egp"), "متوسط سعر الصرف الرسمي السنوي",
     "eg-cbe-exchange"),
    ("NY.GDP.MKTP.KD.ZG", ("نمو الاقتصاد", "نمو الناتج", "gdp growth"), "نمو الناتج المحلي الحقيقي",
     "eg-wdi-gdp"),
    ("FR.INR.LEND", ("سعر الاقراض", "فائدة الاقراض", "lending rate", "lending interest"),
     "سعر الإقراض السنوي", "eg-cbe-interest"),
    (None, ("سعر الفايده", "سعر الفائده", "فايده البنك المركزي", "فائده البنك المركزي",
            "policy rate", "policy interest rate", "cbe rate"), "سعر فائدة البنك المركزي", "eg-cbe-interest"),
    (None, ("تعريفة الكهربا", "تعريفة الكهرباء", "electricity tariff"), "تعريفة الكهرباء", "eg-energy-tariff"),
)
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
COUNTRY_NAMES = {
    "EGY": ("مصر", "egypt", "مصري"),
    "MAR": ("مغرب", "morocco"), "TUN": ("تونس", "tunisia"),
    "JOR": ("اردن", "jordan"), "SAU": ("سعوديه", "saudi arabia"),
    "ARE": ("امارات", "الإمارات", "uae", "united arab emirates"),
    "DZA": ("جزائر", "algeria"), "LBN": ("لبنان", "lebanon"), "IRQ": ("عراق", "iraq"),
    "OMN": ("سلطنة عمان", "oman"), "QAT": ("قطر", "qatar"), "KWT": ("كويت", "kuwait"),
    "BHR": ("بحرين", "bahrain"), "TUR": ("تركيا", "turkey", "türkiye"),
    "KEN": ("كينيا", "kenya"), "NGA": ("نيجيريا", "nigeria"), "ZAF": ("جنوب افريقيا", "south africa"),
    "PAK": ("باكستان", "pakistan"), "IND": ("هند", "india"), "IDN": ("اندونيسيا", "indonesia"),
    "VNM": ("فيتنام", "vietnam"), "BRA": ("برازيل", "brazil"), "MEX": ("مكسيك", "mexico"),
    "USA": ("امريكا", "ولايات متحده", "united states", "usa"), "GBR": ("بريطانيا", "uk", "united kingdom"),
    "DEU": ("المانيا", "germany"), "ESP": ("اسبانيا", "spain"), "FRA": ("فرنسا", "france"),
}
CURRENT = (
    "النهارده", "النهاردة", "اليوم", "دلوقتي", "حاليا", "الحالي", "الحالية", "آخر قراءة", "اخر سعر",
    "أحدث قراءة", "اخر تحديث", "today", "now", "current", "latest",
)
DATED_DETAIL = (
    "يناير", "فبراير", "مارس", "ابريل", "مايو", "يونيو", "يوليو", "اغسطس", "سبتمبر", "اكتوبر",
    "نوفمبر", "ديسمبر", "شهري", "ربع سنوي", "ربعي", "يوم", "شهر", "نهاية السنة", "أول السنة",
    "daily", "monthly", "quarterly", "closing", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december",
)
INCOMPATIBLE = {
    "FP.CPI.TOTL.ZG": ("تضخم اساسي", "اساسي", "core inflation", "core cpi"),
    "PA.NUS.FCRF": ("يورو", "استرليني", "ريال", "درهم", "eur", "gbp", "sar", "aed",
                    "سعر البيع", "سعر الشراء", "buy rate", "sell rate"),
    "NY.GDP.MKTP.KD.ZG": ("اسمي", "nominal"),
}
EXPLANATION = ("اثر", "تأثير", "ليه", "ازاي", "كيف", "ما سبب", "why", "impact", "effect", "what caused")


def has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    padded = " " + text + " "
    return any(" " + phrase + " " in padded for raw in phrases if (phrase := searchable(raw)))


def named_countries(question: str) -> list[str]:
    normalized = searchable(question)
    return [country for country, phrases in COUNTRY_NAMES.items() if has_phrase(normalized, phrases)]


def infer_country(question: str, default: str) -> str:
    countries = named_countries(question)
    return countries[0] if len(countries) == 1 else default


def recent_egypt_cpi(question: str, country: str) -> bool:
    if country != "EGY" or set(named_countries(question)) - {"EGY"}:
        return False
    normalized = searchable(question.translate(DIGITS))
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", normalized))
    if years and years != {"2026"}:
        return False
    if has_phrase(normalized, EXPLANATION + ("دواجن", "فراخ", "كيلو", "ريف", "ريفي", "rural", "poultry")):
        return False
    if not has_phrase(normalized, METRICS[0][1]):
        return False
    if re.search(r"\b2026-10(?:-\d{2})?\b", question.translate(DIGITS)):
        return not re.search(r"\b2026-10-\d{2}\b", question.translate(DIGITS))
    dated_months = DATED_DETAIL[:12] + (
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december",
    )
    months = {month for month in dated_months if has_phrase(normalized, (month,))}
    if months:
        return months <= {"اكتوبر", "october"}
    return has_phrase(normalized, CURRENT + ("اخر تضخم", "احدث تضخم"))


def recent_egypt_source(question: str, country: str) -> str | None:
    if recent_egypt_cpi(question, country):
        return "eg-cbe-cpi-2026-10"
    if country != "EGY" or set(named_countries(question)) - {"EGY"}:
        return None
    dated = question.translate(DIGITS)
    normalized = searchable(dated)
    if has_phrase(normalized, EXPLANATION):
        return None
    if re.search(r"\b2025\s*[/–-]\s*(?:20)?26\b", dated) and has_phrase(
        normalized, METRICS[2][1] + ("الناتج المحلي", "نمو مصر", "نمو المطاعم والفنادق", "gdp")
    ):
        return "eg-mped-activity-2026"
    if has_phrase(normalized, METRICS[4][1] + ("سعر الإيداع لليلة واحدة", "overnight rate")) and (
        has_phrase(normalized, CURRENT + ("نوفمبر 2026", "november 2026"))
        and not re.search(r"\b(?:19\d{2}|20(?!26\b)\d{2})\b", dated)
    ):
        return "eg-cbe-policy-2026-11"
    return None


@dataclass(frozen=True)
class IndicatorMatch:
    reference: str
    message: str = ""
    records: tuple[dict, ...] = ()


def indicator_match(database: Path, question: str, country: str) -> IndicatorMatch | None:
    normalized = searchable(question)
    if has_phrase(normalized, EXPLANATION):
        return None
    found = [(code, title, reference) for code, phrases, title, reference in METRICS if has_phrase(normalized, phrases)]
    if len(found) != 1:
        return None
    code, title, reference = found[0]
    as_ascii = question.translate(DIGITS)
    years = sorted(set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", as_ascii)))
    countries = named_countries(question) or [country]
    egypt_reference = reference if countries == ["EGY"] else ""
    looks_like_undated_quote = not years and code == "PA.NUS.FCRF" and re.search(r"\b(?:بكام|كام|كم)\b", question)
    if has_phrase(normalized, CURRENT) or looks_like_undated_quote:
        return IndicatorMatch(
            egypt_reference, "المتاح عندي بيانات مؤرخة، والقراءة الحالية محتاجة تتراجع من المصدر الرسمي."
        )
    if not years:
        return None
    has_specific_date = re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b", as_ascii)
    if not code or has_phrase(normalized, DATED_DETAIL + INCOMPATIBLE.get(code, ())) or has_specific_date:
        return IndicatorMatch(
            egypt_reference, "البيانات السنوية المتاحة عندي لا تكفي لتحديد الرقم لنوع السعر أو الفترة دي."
        )
    if len(countries) * len(years) > 4:
        return IndicatorMatch(egypt_reference, "حدد لحد أربع مقارنات حسب البلد والسنة في السؤال الواحد.")
    records, missing = [], []
    with closing(connect_readonly(database)) as connection:
        for named in countries:
            for year in years:
                try:
                    observed = lookup_indicator(connection, code, named, int(year))
                except ValueError:
                    missing.append(f"{named} {year}")
                    continue
                text = (
                    f"{title} في {named} لسنة {year}: {observed['value']:.8g} {observed['unit']}. "
                    f"المصدر الأصلي: {observed['source_organization']}. "
                    f"تاريخ تحديث السلسلة في النسخة: {observed['source_last_updated']}."
                )
                records.append({
                    "id": f"wdi-{code.lower()}-{named.lower()}-{year}",
                    "document_id": f"wdi-{code}-{named}-{year}",
                    "kind": "official_observation", "origin": "indicator", "title": title,
                    "published": year, "locator": f"WDI {code} · {named} · {year}",
                    "source": observed["source"], "scope": "قراءة سنوية مؤرخة", "text": text,
                })
    message = f"القراءة غير متاحة في البيانات الحالية للفترات دي ({'، '.join(missing)})." if missing else ""
    return IndicatorMatch(egypt_reference, message, tuple(records))
