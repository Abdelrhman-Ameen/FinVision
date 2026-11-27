"""Route standalone conversational turns before evidence retrieval."""

import re
import unicodedata

from .data_tools.rag_library import ARABIC


# Match the whole turn so that a greeting or thanks followed by a question still reaches RAG.
TURNS = {
    "ar": {
        "greeting": (
            "أهلاً! تحب تسأل عن إيه في مشروعك؟",
            "أهلا|أهلا وسهلا|أهلا بيك|مرحبا|هلا|هاي|هالو|صباح الخير|صباح النور|مساء الخير|مساء النور",
        ),
        "salaam": (
            "وعليكم السلام! تحب تسأل عن إيه في مشروعك؟",
            "سلام عليكم|السلام عليكم|السلام عليكم ورحمة الله|السلام عليكم ورحمة الله وبركاته",
        ),
        "check_in": ("تمام، جاهز أساعدك. تحب تسأل عن إيه؟", "إزيك|إزيك عامل إيه|عامل إيه|كيف حالك|أخبارك إيه|إيه الأخبار"),
        "thanks": ("العفو!", "شكرا|شكرا ليك|شكرا جدا|متشكر|متشكر جدا|تسلم|تسلمي|تسلم إيدك|ميرسي"),
        "acknowledgment": ("تمام.", "تمام|تمام كده|تمام فهمت|ماشي|أوكي|أوك|طيب|آه|أيوه|فهمت|واضح"),
        "farewell": ("مع السلامة!", "باي|مع السلامة|تصبح على خير|أشوفك بعدين"),
        "help": (
            "أقدر أساعدك بأسئلة عن السوق والتمويل والتسعير والتوسع. قولّي بتفكر في إيه.",
            "بتعمل إيه|إنت بتعمل إيه|إنت مين|أقدر أسألك عن إيه|ممكن تساعدني|ساعدني",
        ),
        "clarification": ("اتفضل، قولّي سؤالك.", "عندي سؤال|عندي استفسار|ممكن أسأل|ممكن سؤال|عايز أسأل"),
    },
    "en": {
        "greeting": ("Hi! What would you like to know about your business?", "hello|hi|hey|hi there|hello there|good morning|good afternoon|good evening"),
        "check_in": ("I'm here and ready to help. What would you like to ask?", "how are you|how are you doing|what's up|how's it going"),
        "thanks": ("You're welcome!", "thanks|thank you|thanks a lot|thanks so much|thank you so much|many thanks"),
        "acknowledgment": ("Got it.", "ok|okay|got it|understood|sounds good|makes sense"),
        "farewell": ("See you!", "bye|goodbye|see you|see you later|good night"),
        "help": ("I can help with market research, funding, pricing, and expansion. What are you working on?",
                 "what can you do|who are you|how can you help|can you help me|help me"),
        "clarification": ("Go ahead. What's your question?", "i have a question|can i ask you something|can i ask a question"),
    },
}


def normalize(message: str) -> str:
    text = unicodedata.normalize("NFKC", message)
    text = re.sub(r"[\u0640\u064B-\u065F\u0670\u200B-\u200D'’]", "", text).translate(ARABIC).casefold()
    return " ".join(re.findall(r"[^\W_]+", text))


PHRASES = {
    normalize(phrase): (locale, intent)
    for locale, turns in TURNS.items()
    for intent, (_, variants) in turns.items()
    for phrase in variants.split("|")
}
GREETINGS = tuple(phrase for phrase, (_, intent) in PHRASES.items() if intent in {"greeting", "salaam"})


def conversational_turn(message: str) -> tuple[str, str] | None:
    text = normalize(message)
    if not text:
        return "clarification", TURNS["ar"]["clarification"][0]
    matched = PHRASES.get(text)
    if matched is None:
        # Entirely social combinations such as "أهلاً، إزيك؟" also bypass retrieval.
        for greeting in GREETINGS:
            if text.startswith(greeting + " ") and text[len(greeting) + 1:] in PHRASES:
                matched = PHRASES[greeting]
                break
    if matched:
        locale, intent = matched
        return intent, TURNS[locale][intent][0]
    return None
