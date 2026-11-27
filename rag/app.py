"""Local web UI and API for retrieval-only chat."""

from contextlib import asynccontextmanager, closing
from hashlib import sha256
from pathlib import Path
import re
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .data_tools.rag_library import COUNTRY_TERMS, connect_readonly

from .book_search import book_list_request, book_query
from .conversation import conversational_turn
from .food_decisions import decision_brief, decision_route
from .generation import AnswerBlock, Draft, Gemini, GenerationProblem, Ollama
from .indicators import indicator_match, infer_country, recent_egypt_source
from .retrieval import HybridRetriever
from .settings import Settings


WEB = Path(__file__).with_name("web")
MAX_BODY = 16 * 1024
FOLLOWUP = re.compile(r"^\s*(?:طب\s|طيب\s|وبالنسبه|وبالنسبة|وإيه|وايه|وده\s|what about|and what)", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
NO_EVIDENCE = "المصادر المتاحة مفيهاش دليل كفاية عشان أجاوب على السؤال ده. ممكن تحدد البلد أو القطاع أو الفترة؟"
ERRORS = {
    "api_key_missing": (503, "ضيف مفتاح Gemini في الإعدادات المحلية وشغّل السيرفر من جديد."),
    "provider_credentials": (503, "راجع مفتاح Gemini وصلاحية استخدامه."),
    "provider_quota": (429, "حصة Gemini وصلت للحد الحالي. جرّب السؤال بعد شوية."),
    "provider_timeout": (504, "رد Gemini اتأخر. جرّب السؤال مرة تانية."),
    "provider_unavailable": (502, "Gemini مش متاح دلوقتي. جرّب السؤال مرة تانية."),
    "ollama_unavailable": (503, "موديل Gemma المحلي مش متاح دلوقتي. اتأكد إن Ollama شغّال."),
    "ollama_model_missing": (503, "موديل Gemma المطلوب مش موجود عند Ollama."),
    "ollama_timeout": (504, "رد موديل Gemma المحلي اتأخر. جرّب السؤال مرة تانية."),
    "incomplete_generation": (502, "الرد ماكملش. جرّب سؤال أقصر أو أكثر تحديدًا."),
    "invalid_grounding": (502, "الرد ما قدرش يربط كل نقطة بالمصادر المتاحة. جرّب تعيد صياغة السؤال."),
}
QuestionText = Annotated[str, Field(min_length=1, max_length=900)]


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    question: QuestionText
    previous_questions: Annotated[list[QuestionText], Field(max_length=3)] = Field(default_factory=list)
    country: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "EGY"


def frontend_page(directory: Path) -> str:
    entry = directory / "index.html"
    version = sha256()
    for asset in sorted(directory.rglob("*")):
        if asset.is_file() and asset != entry:
            version.update(asset.relative_to(directory).as_posix().encode())
            version.update(asset.read_bytes())
    return entry.read_text(encoding="utf-8").replace("__ASSET_VERSION__", version.hexdigest()[:16])


def retrieval_query(question: str, previous: list[str]) -> str:
    if not previous or not FOLLOWUP.match(question):
        return question
    earlier = previous[-1]
    if YEAR.search(question):
        earlier = YEAR.sub("", earlier)
    return f"{question}\n{earlier}"


def citation(number: int, record: dict) -> dict:
    parsed = urlsplit(record["source"])
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Non-HTTPS evidence reference")
    excerpt = record["text"]
    if record["origin"] == "curated":
        excerpt = "\n\n".join((record["finding_ar"], record["limit_ar"]))
    return {
        "number": number, "id": record["id"], "title": record["title"], "url": record["source"],
        "kind": record["kind"], "published": record.get("published"), "locator": record["locator"],
        "scope": record.get("scope") or ", ".join(record.get("countries", [])),
        "excerpt": excerpt[:900], "authors": record.get("authors", []),
        "publisher": record.get("publisher"), "license": record.get("license"),
        "rights_holder": record.get("rights_holder"), "modifications": record.get("modifications"),
    }


def reply(draft: Draft, records: list[dict], route: str, related: list[dict] | None = None) -> dict:
    used = sorted({number for block in draft.blocks for number in block.citations})
    return {
        **draft.model_dump(),
        "route": route,
        "sources": [citation(number, records[number - 1]) for number in used],
        "references": [citation(index, item) for index, item in enumerate(related or [], 1)],
    }


def insufficient(message: str) -> Draft:
    return Draft(status="insufficient", blocks=[], limitations=message)


def sourced_books(question: str, records: list[dict]) -> Draft:
    arabic = re.search(r"[\u0621-\u064A]", question)
    message = ("افتح المصدر عشان تقرأ الصفحة اللي جت في البحث وتكمل باقي الكتاب."
               if arabic else "Open a source to read the matched page and continue in the full book.")
    blocks = [
        AnswerBlock(text=" · ".join(part for part in (
            book["title"], book.get("publisher"), str(book.get("published") or "")[:4],
        ) if part), citations=[number])
        for number, book in enumerate(records[:4], 1)
    ]
    return Draft(status="answered", blocks=blocks, limitations=message)


def corpus_stats(database: Path) -> dict:
    with closing(connect_readonly(database)) as db:
        rows, sources = db.execute("SELECT COUNT(*), COUNT(DISTINCT document_id) FROM evidence").fetchone()
        return {
            "indexed_records": rows,
            "searchable_sources": sources,
            "searchable_books": db.execute("SELECT COUNT(*) FROM source_topics WHERE topic = 'type:book-text'").fetchone()[0],
        }


async def body_limit(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "حجم السؤال أو سجل المحادثة أكبر من المسموح.")
        body.extend(chunk)
    return bytes(body)


def create_app(*, settings: Settings | None = None, retriever=None, transport=None) -> FastAPI:
    config = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        library = retriever if retriever is not None else await run_in_threadpool(HybridRetriever, config.cache)
        application.state.corpus_stats = await run_in_threadpool(corpus_stats, library.database)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(38, connect=8), transport=transport, follow_redirects=False,
            trust_env=config.provider != "ollama",
        ) as client:
            application.state.library = library
            application.state.generator = (
                Ollama(config.ollama_url, config.model, client)
                if config.provider == "ollama" else Gemini(config.api_key, config.model, client)
            )
            yield

    api = FastAPI(title="ESIS evidence chat", lifespan=lifespan, docs_url=None, redoc_url=None)
    api.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    api.mount("/assets", StaticFiles(directory=WEB), name="assets")

    @api.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @api.get("/")
    def homepage():
        return HTMLResponse(frontend_page(WEB))

    @api.get("/api/status")
    async def status(request: Request):
        return {
            "model": config.model,
            "provider": config.provider,
            "ready": await request.app.state.generator.ready(),
            "api_configured": bool(config.api_key),
            **request.app.state.corpus_stats,
        }

    @api.post("/api/chat")
    async def chat(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            raise HTTPException(403, "افتح الشات من نفس عنوان السيرفر المحلي.")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise HTTPException(415, "الطلب لازم يكون بصيغة JSON.")
        raw = await body_limit(request)
        try:
            payload = ChatInput.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(422, "اكتب سؤال من حرف لحد 900 حرف.") from exc
        if payload.country not in COUNTRY_TERMS:
            raise HTTPException(422, "اختار بلد من البيانات المتاحة.")

        if turn := conversational_turn(payload.question):
            intent, text = turn
            return {
                "status": "answered", "route": "conversation", "intent": intent,
                "blocks": [{"text": text, "citations": []}], "limitations": "",
                "sources": [], "references": [],
            }

        previous = [item for item in payload.previous_questions if not conversational_turn(item)]
        library = request.app.state.library
        query = retrieval_query(payload.question, previous)
        country = infer_country(query, payload.country)
        if recent := library.by_id.get(recent_egypt_source(query, country)):
            answer = Draft(status="answered", blocks=[AnswerBlock(text=recent["finding_ar"], citations=[1])],
                           limitations=recent["limit_ar"])
            return reply(answer, [recent], "dated_indicator")
        metric = await run_in_threadpool(indicator_match, library.database, query, country)
        if metric:
            if metric.records:
                answer = Draft(
                    status="partial" if metric.message else "answered",
                    blocks=[
                        AnswerBlock(text=record["text"], citations=[index])
                        for index, record in enumerate(metric.records, 1)
                    ],
                    limitations=metric.message or "دي قراءات سنوية تاريخية. للمقارنة برقم شهري راجع تعريف المؤشر والفترة.",
                )
                return reply(answer, list(metric.records), "historical_indicator")
            related = [library.by_id[metric.reference]] if metric.reference in library.by_id else []
            return reply(insufficient(metric.message), [], "indicator_unavailable", related)

        retrieved = await run_in_threadpool(library.search, query, country)
        if not retrieved:
            return reply(insufficient(NO_EVIDENCE), [], "retrieval")
        if book_list_request(query):
            return reply(sourced_books(payload.question, retrieved), retrieved, "book_lookup")
        route = decision_route(query)
        brief_route = f"food_{route}" if route in {"branch", "profit"} else route
        try:
            draft = await request.app.state.generator.respond(payload.question, previous, retrieved)
        except GenerationProblem as exc:
            if brief := decision_brief(route, payload.question, retrieved):
                return reply(brief, retrieved, brief_route)
            if book_query(query):
                brief = sourced_books(payload.question, retrieved)
                brief.status = "partial"
                return reply(brief, retrieved, "book_lookup")
            status_code, message = ERRORS[exc.code]
            return JSONResponse({"error": {"code": exc.code, "message": message}}, status_code=status_code)
        if brief := decision_brief(route, payload.question, retrieved, draft):
            return reply(brief, retrieved, brief_route)
        if draft.status == "insufficient" and (
            not draft.limitations or (re.search(r"[\u0621-\u064A]", payload.question)
                                      and not re.search(r"[\u0621-\u064A]", draft.limitations))
        ):
            draft = insufficient(NO_EVIDENCE)
        return reply(draft, retrieved, "retrieval")

    return api


app = create_app()
