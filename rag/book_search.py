"""Query terms for looking up books and textbook passages in both supported languages."""

from .data_tools.rag_library import searchable

from .indicators import has_phrase


BOOK_REQUEST = ("كتاب", "كتب", "مراجع أكاديمية", "textbook", "textbooks", "book", "books", "monographs")
TRANSLATIONS = (
    (("مصر", "مصري", "المصرية", "egypt", "egyptian"), "Egypt Egyptian"),
    (("اقتصاد", "اقتصادي", "اقتصادية", "اقتصاديه"), "economy macroeconomic growth development"),
    (("بزنس", "بزنيس", "ريادة", "مشروع", "شركات", "استراتيجيات", "إدارة"), "business strategy entrepreneurship management"),
    (("تسويق", "مبيعات", "تسعير", "سعر"), "marketing sales pricing"),
    (("تمويل", "سيولة", "استثمار", "ميزانية", "محاسبة", "تكلفة"), "finance cash flow investment budgeting accounting cost"),
    (("دخل", "أسر", "إنفاق", "شراء", "استهلاك", "معيشة"), "household income expenditure consumption"),
    (("توريد", "تشغيل", "مخزون", "إنتاج"), "operations procurement inventory production"),
    (("تصدير", "تجارة", "جمرك"), "exports trade customs"),
    (("غذاء", "فراخ", "مطعم", "تشيكن", "مطاعم"), "food restaurant kitchen menu"),
    (("موظفين", "توظيف", "مرتبات", "عمالة"), "jobs workforce employment human resources"),
)


def book_query(question: str) -> tuple[str, bool] | None:
    normalized = searchable(question)
    if not has_phrase(normalized, BOOK_REQUEST):
        return None
    translated = [terms for phrases, terms in TRANSLATIONS if has_phrase(normalized, phrases)]
    return " ".join([question, *translated]), has_phrase(normalized, TRANSLATIONS[0][0])


def book_list_request(question: str) -> bool:
    if book_query(question) is None:
        return False
    return not has_phrase(searchable(question), (
        "اشرح", "لخص", "ملخص", "بتقول ايه", "بيقول ايه", "حلل", "summarize", "explain", "what does",
    ))
