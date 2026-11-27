"""Match expansion and food-service decisions to practical evidence."""

import re

from .data_tools.rag_library import searchable

from .generation import AnswerBlock, Draft
from .indicators import has_phrase


FOOD = ("مطعم", "مطاعم", "المطعم", "المطاعم", "لمطعم", "للمطعم", "لمطاعم", "للمطاعم",
        "فراخ", "فرايد تشيكن", "تشيكن", "دواجن", "كافيه",
        "restaurant", "restaurants", "fried chicken", "chicken shop", "cafe")
PROFIT = ("بتكسب", "بيكسب", "تكسب", "ربح", "أرباح", "ربحية", "مربح", "مربحة", "هامش",
          "نقطة التعادل", "المكسب", "break even", "profitable", "profitability", "profit", "margin")
BRANCH = ("فرع", "فروع", "أتوسع", "توسع", "افتتاح", "أفتح", "مكان جديد", "location", "branch", "expansion", "expand")
PROFIT_SOURCES = ("food-menu-margin", "food-break-even", "eg-cbe-poultry-2026-10", "food-standard-cost")
BRANCH_SOURCES = ("food-location-demand", "food-break-even", "eg-cbe-poultry-2026-10", "food-standard-cost")
EXPANSION_SOURCES = BRANCH_SOURCES[:2]
BRANCH_OPENING = (
    "أفتح فرع", "أفتح الفرع", "نفتح فرع", "نفتح الفرع", "يفتح فرع", "تفتح فرع",
    "فتح فرع", "افتتاح فرع", "افتتاح الفرع", "التوسع بفروع",
    "open a branch", "open another branch", "open a new branch", "open a second branch",
    "opening a branch", "opening a new branch", "opening a second branch",
    "open a new location", "opening a new location",
)


def food_route(question: str) -> str | None:
    normalized = searchable(question)
    if not has_phrase(normalized, FOOD):
        return None
    if has_phrase(normalized, PROFIT):
        return "profit"
    if has_phrase(normalized, BRANCH):
        return "branch"
    return None


def decision_route(question: str) -> str | None:
    if route := food_route(question):
        return route
    if has_phrase(searchable(question), BRANCH_OPENING):
        return "business_expansion"
    return None


def search_profile(route: str | None, country: str) -> tuple[tuple[str, ...], str, str | None]:
    if route is None:
        return (), "", None
    if route == "business_expansion":
        return EXPANSION_SOURCES, "market demand competitor analysis site selection break even fixed variable costs", None
    anchors = PROFIT_SOURCES if route == "profit" else BRANCH_SOURCES
    if country != "EGY":
        anchors = tuple(key for key in anchors if not key.startswith("eg-"))
    if route == "profit":
        return anchors, "restaurant menu engineering recipe cost break even poultry input prices", "price-change"
    return anchors, "restaurant location demand pilot orders competitive analysis break even", "branch"


def decision_brief(route: str | None, question: str, records: list[dict], draft: Draft | None = None) -> Draft | None:
    if route != "business_expansion":
        return food_brief(route, question, records, draft)
    numbers = {record["id"]: index for index, record in enumerate(records, 1)}
    if not all(key in numbers for key in EXPANSION_SOURCES):
        return None
    demand, break_even = [numbers[key] for key in EXPANSION_SOURCES]
    if draft and draft.status != "insufficient":
        cited = {number for block in draft.blocks for number in block.citations}
        body = " ".join(block.text for block in draft.blocks if break_even in block.citations)
        calculation = "÷" in body or has_phrase(
            searchable(body), ("اقسم", "مقسوم", "مقسومة", "بقسمة", "تقسم", "divide", "divided by")
        )
        if {demand, break_even} <= cited and calculation:
            return None
    if re.search(r"[\u0621-\u064A]", question):
        market = "ابدأ بمقارنة المناطق المرشحة. شوف العملاء المحتملين فين، بيوصلوا للمكان إزاي، وبيشتروا من مين وبكام. اتكلم مع عملاء في كل منطقة أو اعمل تجربة بيع صغيرة قبل ما تلتزم بالإيجار."
        costs = "احسب الإيجار والمرتبات الثابتة وباقي مصاريف الفرع الشهرية. اطرح التكلفة المتغيرة من صافي سعر البيع عشان تعرف هامش كل عملية بيع. لو الهامش موجب، اقسم مصاريف الشهر الثابتة عليه عشان تعرف محتاج كام عملية بيع للتعادل. قارنها بالطلب اللي قسته، واحسب تكلفة تجهيز الفرع لوحدها."
        missing = "قولّي نوع النشاط والمناطق اللي بتقارن بينها. لو معاك الإيجار المتوقع ومتوسط سعر البيع والتكلفة المتغيرة، نقدر نحسب التعادل لكل مكان."
    else:
        market = "Compare candidate locations on customer demand, accessibility, competitor prices, and available alternatives. Interview customers or run a small sales pilot before committing to a lease."
        costs = "Add up monthly fixed costs such as rent and salaries. Subtract variable cost from the net selling price to get contribution per sale. If it is positive, divide monthly fixed costs by it to estimate break-even sales volume. Compare that volume with measured demand and budget opening costs separately."
        missing = "Share your business type, candidate locations, expected monthly fixed costs, and contribution per sale to compare the locations."
    return Draft(
        status="partial",
        blocks=[AnswerBlock(text=market, citations=[demand]), AnswerBlock(text=costs, citations=[break_even])],
        limitations=missing,
    )


def food_brief(route: str | None, question: str, records: list[dict], draft: Draft | None = None) -> Draft | None:
    if route is None or not re.search(r"[\u0621-\u064A]", question):
        return None
    numbers = {record["id"]: index for index, record in enumerate(records, 1)}
    if route == "branch":
        required = ("food-location-demand", "food-break-even")
        if not all(key in numbers for key in required):
            return None
        if draft and draft.status != "insufficient":
            cited = {number for block in draft.blocks for number in block.citations}
            if {numbers[key] for key in required} <= cited:
                return None
        return Draft(
            status="partial",
            blocks=[
                AnswerBlock(
                    text="لو عندك أكتر من فرع، ابدأ بمبيعات الفروع اللي عندك وعناوين التوصيل. دور على منطقة قريبة من طلب موجود ولسه وصولك ليها ضعيف. قارن الأماكن المرشحة بأسعار المنافسين، ووقت التوصيل، والإيجار، وبعد كده اعمل تجربة بيع تقيس الطلبات المدفوعة.",
                    citations=[numbers["food-location-demand"]],
                ),
                AnswerBlock(
                    text="احسب كام طلب لازم تبيع في كل مكان عشان تغطي تكاليفه الثابتة. اقسم مصاريفه الشهرية على متوسط هامش الطلب بعد المكونات والتغليف والعمولة. قارن المطلوب بالمبيعات في التجربة، وخد في الحسبان الطلب اللي هيتنقل من فرع قديم للفرع الجديد.",
                    citations=[numbers["food-break-even"]],
                ),
            ],
            limitations="ابعت أسماء المناطق اللي بتفكر فيها، ومبيعات فروعك وطلبات التوصيل حسب المنطقة. ساعتها نقدر نقارن الأماكن بالأرقام.",
        )
    required = ("food-menu-margin", "food-break-even", "food-standard-cost")
    if not all(key in numbers for key in required):
        return None
    if draft and draft.status != "insufficient":
        cited = {number for block in draft.blocks for number in block.citations}
        body = " ".join(block.text for block in draft.blocks)
        text = searchable(body)
        calculation = "÷" in body or has_phrase(text, ("اقسم", "مقسوم", "بقسمة", "قسمة", "تقسم"))
        local = numbers.get("eg-cbe-poultry-2026-10")
        if ({numbers["food-break-even"], numbers["food-menu-margin"]} <= cited
                and has_phrase(text, ("هامش",)) and has_phrase(text, ("تعادل",)) and calculation
                and (local is None or (local in cited and "0.27" in body))):
            return None
    blocks = [
        AnswerBlock(
            text="محل الفراخ ممكن يكسب لو هامش الطلب وعدد الطلبات بيغطّوا مصاريفه. احسب اللي بيفضل من صافي سعر البيع بعد تكلفة الفراخ وباقي المكونات والهالك والتغليف وأي عمولة بتتخصم على الطلب. لو بتبيع فراخ نيّة، اعمل نفس الحساب لكل كيلو قابل للبيع بدل الوجبة.",
            citations=[numbers["food-menu-margin"], numbers["food-standard-cost"]],
        ),
        AnswerBlock(
            text="اقسم مصاريفك الثابتة في الشهر، زي الإيجار والمرتبات الثابتة، على متوسط هامش الطلب لو هو موجب. ده عدد الطلبات المطلوب عشان تغطي التشغيل. قارن الرقم بعدد الطلبات في تجربة بيع من المكان، وأعد الحساب بسعر توريد أعلى. لو الطلب الفعلي ما بيغطيش التعادل، اختبر تغيير السعر أو التكلفة قبل ما تكبر أو تسيب الفكرة.",
            citations=[numbers["food-break-even"]],
        ),
    ]
    if number := numbers.get("eg-cbe-poultry-2026-10"):
        blocks.append(AnswerBlock(
            text="في مصر، نشرة البنك المركزي لأكتوبر 2026 رصدت ارتفاع أسعار الدواجن اللي ساهم بـ0.27 نقطة مئوية في التضخم الشهري في المدن. عشان تعرف أثره عليك، استخدم عرض سعر مورّد في منطقتك وفترة التوريد اللي هتشتري فيها.",
            citations=[number],
        ))
    return Draft(
        status="partial", blocks=blocks,
        limitations="قولّي هتبيع فراخ نيّة ولا وجبات، وفي أنهي منطقة، وصافي سعر البيع والتكلفة المتغيرة ومصاريف الشهر. بالأرقام دي نقدر نحسب التعادل ونقارن الفكرة ببديل بنفس الطريقة.",
    )
