import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from rag.data_tools.rag_library import build_index, connect_readonly, rank
from rag.app import create_app, frontend_page
from rag.book_search import book_query
from rag.food_decisions import decision_route, food_route, search_profile
from rag.generation import Ollama
from rag.settings import Settings


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    file = tmp_path_factory.mktemp("rag-chat") / "source.sqlite"
    build_index(file)
    return file


class TestLibrary:
    __test__ = False

    def __init__(self, database, empty=False):
        self.database = database
        self.queries = []
        with closing(connect_readonly(database)) as db:
            self.by_id = {
                identifier: json.loads(payload) for identifier, payload in db.execute(
                    "SELECT id, payload FROM evidence WHERE id IN (?, ?)",
                    ("eg-microfinance-rct", "eg-cbe-exchange"),
                )
            }
        self.records = [] if empty else [self.by_id["eg-microfinance-rct"]]

    def search(self, question, country):
        self.queries.append((question, country))
        return self.records


def candidate(text="في تجربة مصرية، اتقارن القرض بالمنحة.", citations=(1,), status="answered"):
    return {
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": json.dumps({
                "status": status,
                "blocks": [{"text": text, "citations": list(citations)}] if status != "insufficient" else [],
                "limitations": "التجربة تخص المشاركين فيها." if status != "insufficient" else "المصادر غير كافية.",
            })}]},
        }],
    }


def chat_client(database, handler, key="unit-test-key", empty=False):
    library = TestLibrary(database, empty=empty)
    application = create_app(settings=Settings(api_key=key), retriever=library, transport=httpx.MockTransport(handler))
    return TestClient(application, base_url="http://localhost"), library


def test_end_to_end_chat_has_numbered_trusted_sources_and_no_key_in_content(database):
    sent = []

    def provider(request):
        sent.append(request)
        assert request.headers["x-goog-api-key"] == "unit-test-key"
        assert request.url.host == "generativelanguage.googleapis.com"
        outbound = json.loads(request.content)
        assert "unit-test-key" not in json.dumps(outbound)
        evidence = json.loads(outbound["contents"][0]["parts"][0]["text"])["evidence"]
        assert evidence[0]["number"] == 1
        assert "مصر" in evidence[0]["excerpt"]
        assert "tools" not in outbound
        return httpx.Response(200, json=candidate())

    client, library = chat_client(database, provider)
    with client:
        page = client.get("/")
        assert page.status_code == 200
        assert "دليل المؤسس" in page.text
        assert client.get("/assets/chat.js").status_code == 200
        status = client.get("/api/status")
        assert status.json()["api_configured"] is True
        assert "unit-test-key" not in status.text
        result = client.post("/api/chat", json={"question": "فيه دراسة عن القروض والمنح في مصر؟"})
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
    body = result.json()
    assert body["route"] == "retrieval"
    assert body["blocks"][0]["citations"] == [1]
    assert body["sources"][0]["id"] == "eg-microfinance-rct"
    assert body["sources"][0]["url"].startswith("https://www.povertyactionlab.org/")
    assert len(library.queries) == len(sent) == 1


@pytest.mark.parametrize("numbers", [(2,), (0,), ()])
def test_bad_or_missing_citations_never_reach_chat(database, numbers):
    client, _ = chat_client(database, lambda _: httpx.Response(200, json=candidate(citations=numbers)))
    with client:
        response = client.post("/api/chat", json={"question": "إيه أثر التمويل؟"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] in {"invalid_grounding", "incomplete_generation"}
    assert "blocks" not in response.json()


def test_empty_retrieval_does_not_spend_gemini_quota(database):
    client, _ = chat_client(database, lambda _: pytest.fail("Unexpected Gemini call"), empty=True)
    with client:
        result = client.post("/api/chat", json={"question": "سؤال خارج التغطية"}).json()
    assert result["status"] == "insufficient"
    assert result["sources"] == result["blocks"] == []


def test_model_can_abstain_and_does_not_show_unrelated_citations(database):
    client, _ = chat_client(database, lambda _: httpx.Response(200, json=candidate(status="insufficient")))
    with client:
        result = client.post("/api/chat", json={"question": "نامت إمتى القطط النهاردة؟"}).json()
    assert result["status"] == "insufficient"
    assert result["sources"] == result["blocks"] == []


@pytest.mark.parametrize(("message", "intent", "text"), [
    ("اهلا", "greeting", "أهلاً!"),
    ("أهــلاً، إزيّك؟ 👋", "greeting", "أهلاً!"),
    ("السَّلام عليكم ورحمة الله وبركاته", "salaam", "وعليكم السلام!"),
    ("إزيك؟", "check_in", "جاهز أساعدك"),
    ("تمام، فهمت", "acknowledgment", "تمام."),
    ("Thanks!", "thanks", "You're welcome!"),
    ("؟", "clarification", "قولّي سؤالك"),
])
def test_conversation_bypasses_retrieval_and_generation(database, message, intent, text):
    client, library = chat_client(database, lambda _: pytest.fail("Unexpected generation for conversation"))
    with client:
        response = client.post("/api/chat", json={
            "question": message, "previous_questions": ["فيه بحث مصري عن القروض والمنح؟"],
        })
    assert response.status_code == 200
    answer = response.json()
    assert answer["route"] == "conversation"
    assert answer["intent"] == intent
    assert text in answer["blocks"][0]["text"]
    assert answer["blocks"][0]["citations"] == answer["sources"] == answer["references"] == []
    assert library.queries == []


@pytest.mark.parametrize("message", ["أهلاً، إزاي أحسب نقطة التعادل؟", "التمويل", "Hello, how do I price my products?"])
def test_business_request_still_uses_rag(database, message):
    client, library = chat_client(database, lambda _: httpx.Response(200, json=candidate()))
    with client:
        response = client.post("/api/chat", json={"question": message})
    assert response.status_code == 200
    assert response.json()["route"] == "retrieval"
    assert library.queries == [(message, "EGY")]


def test_historical_and_latest_lookup_do_not_call_gemini(database):
    client, _ = chat_client(database, lambda _: pytest.fail("Unexpected Gemini call"))
    with client:
        historical = client.post("/api/chat", json={"question": "كام التضخم في مصر سنة ٢٠٢٤؟"}).json()
        assert historical["route"] == "historical_indicator"
        assert "28.27059" in historical["blocks"][0]["text"]
        assert "date=2024" in historical["sources"][0]["url"]
        today = client.post("/api/chat", json={"question": "سعر الدولار النهاردة في مصر؟"}).json()
        assert today["route"] == "indicator_unavailable"
        assert today["status"] == "insufficient"
        assert today["references"][0]["id"] == "eg-cbe-exchange"
        monthly = client.post("/api/chat", json={"question": "كام التضخم في يناير 2024؟"}).json()
        assert monthly["status"] == "insufficient"
        assert monthly["blocks"] == []


def test_egypt_october_cpi_uses_the_dated_local_source(database):
    library = TestLibrary(database)
    with closing(connect_readonly(database)) as db:
        payload, = db.execute("SELECT payload FROM evidence WHERE id = 'eg-cbe-cpi-2026-10'").fetchone()
        library.by_id["eg-cbe-cpi-2026-10"] = json.loads(payload)
    app = create_app(settings=Settings(), retriever=library)
    with TestClient(app, base_url="http://localhost") as client:
        answer = client.post("/api/chat", json={"question": "كام التضخم في مصر في أكتوبر ٢٠٢٦؟"}).json()
        assert answer["route"] == "dated_indicator"
        assert "12.7%" in answer["blocks"][0]["text"]
        assert answer["sources"][0]["published"] == "2026-11"
        historical = client.post("/api/chat", json={"question": "كام التضخم في مصر في أكتوبر 2024؟"}).json()
        assert historical["status"] == "insufficient"


def test_egyptian_chicken_shop_profit_question_returns_operating_steps_when_model_abstains(database):
    question = "هل محلات الفراخ بتكسب في مصر ولا افكر في فكرة جديدة؟"
    assert food_route(question) == "profit"
    assert food_route("إيه أثر الدولار على محلات الملابس في مصر؟") is None
    anchors, _, _ = search_profile(food_route(question), "EGY")
    library = TestLibrary(database)
    with closing(connect_readonly(database)) as db:
        library.records = [
            json.loads(db.execute("SELECT payload FROM evidence WHERE id = ?", (identity,)).fetchone()[0])
            for identity in anchors
        ]
    app = create_app(settings=Settings(api_key="unit-test-key"), retriever=library,
                     transport=httpx.MockTransport(lambda _: httpx.Response(200, json=candidate(status="insufficient"))))
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/chat", json={"question": question}).json()
    assert response["status"] == "partial"
    assert "هامش" in response["blocks"][0]["text"]
    assert "0.27 نقطة مئوية" in response["blocks"][2]["text"]
    assert {source["id"] for source in response["sources"]} == set(anchors)


@pytest.mark.parametrize(("question", "expected_text"), [
    ("أدرس إيه قبل ما أفتح فرع جديد؟", "للتعادل"),
    ("أفكر أفتح فرع لمحل ملابس في مدينة نصر، أبدأ بإيه؟", "للتعادل"),
    ("What should I check before opening a second branch?", "break-even"),
])
def test_branch_opening_uses_cited_guidance_when_generation_has_invalid_citations(database, question, expected_text):
    route = decision_route(question)
    assert route == "business_expansion"
    assert decision_route("فين أقرب فرع للبنك؟") is None
    assert decision_route("أفتح فرع جديد لمطعم؟") == "branch"
    library = TestLibrary(database)
    anchors, _, _ = search_profile(route, "EGY")
    with closing(connect_readonly(database)) as db:
        library.records = [
            json.loads(db.execute("SELECT payload FROM evidence WHERE id = ?", (identifier,)).fetchone()[0])
            for identifier in anchors
        ]
    invalid = candidate(citations=(-1,))["candidates"][0]["content"]["parts"][0]["text"]
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, json={"done": True, "done_reason": "stop", "message": {"content": invalid}}
    ))
    settings = Settings(model="gemma3:4b", provider="ollama")
    application = create_app(settings=settings, retriever=library, transport=transport)
    with TestClient(application, base_url="http://localhost") as client:
        result = client.post("/api/chat", json={"question": question})
    assert result.status_code == 200
    body = result.json()
    assert body["route"] == route
    assert body["status"] == "partial"
    assert [source["id"] for source in body["sources"]] == list(anchors)
    assert [block["citations"] for block in body["blocks"]] == [[1], [2]]
    assert expected_text in body["blocks"][1]["text"]


def test_arabic_book_request_adds_bilingual_subject_terms():
    query, egypt_scope = book_query("عندك كتب عن الاقتصاد المصري؟")
    assert egypt_scope and "Egypt Egyptian economy macroeconomic" in query
    assert book_query("كام التضخم في مصر؟") is None


def test_book_lookup_returns_distinct_egyptian_book_pages_without_generation(database):
    question = "عايز كتب عن الاقتصاد المصري وخصوصا دخل الأسر وتصرفات الشراء"
    query, egypt_scope = book_query(question)
    library = TestLibrary(database)
    with closing(connect_readonly(database)) as db:
        library.records = rank(db, query, limit=6, book_search=True, egypt_books=egypt_scope)
    app = create_app(settings=Settings(api_key="unit-test-key"), retriever=library,
                     transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected generation for a catalog lookup")))
    with TestClient(app, base_url="http://localhost") as client:
        body = client.post("/api/chat", json={"question": question}).json()
    assert body["route"] == "book_lookup"
    assert body["status"] == "answered"
    assert any("Inside Inequality" in source["title"] for source in body["sources"])
    assert all("#page=" in source["url"] for source in body["sources"])
    assert len({source["title"] for source in body["sources"]}) == len(body["sources"])


def test_numeric_lookup_respects_comparison_currency_and_coverage(database):
    client, _ = chat_client(database, lambda _: pytest.fail("Unexpected Gemini call"))
    with client:
        comparison = client.post("/api/chat", json={"question": "قارن التضخم بين مصر والمغرب سنة 2024"}).json()
        assert comparison["status"] == "answered"
        assert {source["id"] for source in comparison["sources"]} == {
            "wdi-fp.cpi.totl.zg-egy-2024", "wdi-fp.cpi.totl.zg-mar-2024",
        }
        core = client.post("/api/chat", json={"question": "التضخم الأساسي في مصر سنة 2024؟"}).json()
        assert core["status"] == "insufficient"
        euro = client.post("/api/chat", json={"question": "سعر الصرف من اليورو للجنيه في 2024؟"}).json()
        assert euro["status"] == "insufficient"
        outside = client.post("/api/chat", json={"question": "التضخم في أمريكا سنة 2024؟"}).json()
        assert outside["status"] == "insufficient"
        assert outside["sources"] == []


def test_followup_reuses_previous_topic_for_retrieval(database):
    client, library = chat_client(database, lambda _: httpx.Response(200, json=candidate()))
    with client:
        result = client.post("/api/chat", json={
            "question": "طب العينة كانت مين؟",
            "previous_questions": ["فيه بحث مصري عن القروض والمنح؟", "تمام", "شكراً"],
        })
    assert result.status_code == 200
    assert "القروض والمنح" in library.queries[0][0]
    assert "شكراً" not in library.queries[0][0]


def test_quota_failure_is_clear_and_secrets_are_not_in_error(database):
    client, _ = chat_client(database, lambda _: httpx.Response(429, json={"message": "server included unit-test-key"}))
    with client:
        result = client.post("/api/chat", json={"question": "إيه الدراسات المتاحة؟"})
    assert result.status_code == 429
    assert result.json()["error"]["code"] == "provider_quota"
    assert "unit-test-key" not in result.text


def test_local_origin_payload_and_missing_key_errors(database):
    client, _ = chat_client(database, lambda _: pytest.fail("Unexpected Gemini call"), key="")
    with client:
        assert client.post("/api/chat", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
        assert client.post("/api/chat", json={"question": "  "}).status_code == 422
        assert client.post("/api/chat", json={"question": "?" * 901}).status_code == 422
        assert client.post("/api/chat", json={"question": "?" * 20_000}).status_code == 413
        assert client.post("/api/chat", json={"question": "test"}, headers={"Origin": "https://example.com"}).status_code == 403
        response = client.post("/api/chat", json={"question": "إيه أبحاث التمويل؟"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "api_key_missing"


def test_ollama_serves_cited_answers_without_a_cloud_request(database):
    paths = []

    def provider(request):
        assert request.url.host == "127.0.0.1"
        assert "x-goog-api-key" not in request.headers
        paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma3:4b"}]})
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "gemma3:4b"
        assert body["format"]["type"] == "object"
        citation_items = body["format"]["$defs"]["AnswerBlock"]["properties"]["citations"]["items"]
        assert citation_items["enum"] == [1]
        assert json.loads(body["messages"][1]["content"])["evidence"][0]["number"] == 1
        text = candidate()["candidates"][0]["content"]["parts"][0]["text"]
        return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {"content": text}})

    settings = Settings(model="gemma3:4b", provider="ollama")
    application = create_app(
        settings=settings, retriever=TestLibrary(database), transport=httpx.MockTransport(provider)
    )
    with TestClient(application, base_url="http://localhost") as client:
        status = client.get("/api/status").json()
        assert status["provider"] == "ollama" and status["ready"] is True
        response = client.post("/api/chat", json={"question": "فيه دراسة عن التمويل؟"})
    assert response.status_code == 200
    assert response.json()["sources"][0]["id"] == "eg-microfinance-rct"
    assert paths == ["/api/tags", "/api/chat"]


def test_ollama_missing_model_reports_unavailable(database):
    settings = Settings(model="gemma3:4b", provider="ollama")
    application = create_app(
        settings=settings, retriever=TestLibrary(database),
        transport=httpx.MockTransport(lambda _: httpx.Response(404)),
    )
    with TestClient(application, base_url="http://localhost") as client:
        assert client.get("/api/status").json()["ready"] is False
        response = client.post("/api/chat", json={"question": "فيه دراسة عن التمويل؟"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ollama_model_missing"


def test_ollama_rejects_nonlocal_endpoints():
    with pytest.raises(ValueError, match="loopback"):
        Ollama("https://example.com", "gemma3:4b", None)


def test_page_uses_versioned_assets_and_revalidates_static_files(database, tmp_path):
    client, _ = chat_client(database, lambda _: pytest.fail("Unexpected Gemini call"))
    with client:
        page = client.get("/")
        assert page.headers["cache-control"] == "no-store"
        css_url = page.text.split('href="/assets/chat.css?v=', 1)[1].split('"', 1)[0]
        assert len(css_url) == 16
        assert f"/assets/chat.js?v={css_url}" in page.text
        asset = client.get(f"/assets/chat.css?v={css_url}")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-cache"

    (tmp_path / "index.html").write_text("__ASSET_VERSION__", encoding="utf-8")
    asset_path = tmp_path / "chat.css"
    asset_path.write_text("old", encoding="utf-8")
    old_version = frontend_page(tmp_path)
    asset_path.write_text("new", encoding="utf-8")
    assert frontend_page(tmp_path) != old_version
