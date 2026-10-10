import base64
import hashlib
import hmac
import json

import httpx
import pytest
from PIL import Image

from ai_video.errors import ProviderError, Rejected, classify
from ai_video.providers.ark import ArkImage, ArkVideo
from ai_video.providers.base import ImageRequest, SpeechRequest, VideoRequest
from ai_video.providers.doubao_tts import DoubaoTTS
from ai_video.providers.gemini import GeminiImage, GeminiTTS
from ai_video.providers.kling import KlingVideo, jwt_token
from ai_video.providers.minimax import MiniMaxTTS, MiniMaxVideo


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "frame.png"
    Image.new("RGB", (64, 36), "red").save(path)
    return path


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _pad(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def test_classify_moderation_messages():
    assert classify("InputImageSensitiveContentDetected", "") is Rejected
    assert classify("1026", "video description contains sensitive content") is Rejected
    assert classify("", "人脸审核未通过") is Rejected
    assert classify("InternalServiceError", "interface timeout") is not Rejected


def test_ark_video_first_last_frame_body_and_poll(monkeypatch, frame):
    monkeypatch.setenv("ARK_API_KEY", "k")
    p = ArkVideo("seedance", {"key_env": "ARK_API_KEY", "base_url": "https://ark.test/api/v3", "model": "m",
                              "resolution": "1080p"})
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            assert req.url.path == "/api/v3/contents/generations/tasks"
            seen["body"] = json.loads(req.content)
            seen["auth"] = req.headers["authorization"]
            return httpx.Response(200, json={"id": "t1"})
        assert req.url.path == "/api/v3/contents/generations/tasks/t1"
        return httpx.Response(200, json={"status": "succeeded", "content": {"video_url": "https://cdn/v.mp4"},
                                         "usage": {"completion_tokens": 10}})

    with client(handler) as http:
        req = VideoRequest("prompt", duration=5, ratio="16:9", first_frame=frame, last_frame=frame, audio=False)
        assert p.submit(http, req) == "t1"
        status = p.poll(http, "t1")
    body = seen["body"]
    assert seen["auth"] == "Bearer k"
    assert [c.get("role") for c in body["content"]] == [None, "first_frame", "last_frame"]
    assert body["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert body["ratio"] == "adaptive" and body["generate_audio"] is False and body["resolution"] == "1080p"
    assert status.state == "done" and status.url == "https://cdn/v.mp4"


def test_ark_text_to_video_keeps_ratio(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    p = ArkVideo("seedance", {"key_env": "ARK_API_KEY", "base_url": "https://ark.test/api/v3", "model": "m"})
    body = p.build_body(VideoRequest("prompt", ratio="9:16"))
    assert body["ratio"] == "9:16" and len(body["content"]) == 1 and "resolution" not in body


def test_ark_submit_refusal_raises_rejected(monkeypatch, frame):
    monkeypatch.setenv("ARK_API_KEY", "k")
    p = ArkVideo("seedance", {"key_env": "ARK_API_KEY", "base_url": "https://ark.test/api/v3", "model": "m"})

    def handler(req):
        return httpx.Response(400, json={"error": {"code": "InputImageSensitiveContentDetected.PrivacyInformation",
                                                   "message": "The request failed because the input image may contain real person"}})

    with client(handler) as http, pytest.raises(Rejected):
        p.submit(http, VideoRequest("p", first_frame=frame))


@pytest.mark.parametrize("code,state", [("OutputVideoSensitiveContentDetected", "rejected"),
                                        ("InternalServiceError", "failed")])
def test_ark_poll_separates_refusals_from_failures(monkeypatch, code, state):
    monkeypatch.setenv("ARK_API_KEY", "k")
    p = ArkVideo("seedance", {"key_env": "ARK_API_KEY", "base_url": "https://ark.test/api/v3", "model": "m"})

    def handler(req):
        return httpx.Response(200, json={"status": "failed", "error": {"code": code, "message": "x"}})

    with client(handler) as http:
        assert p.poll(http, "t1").state == state


def test_ark_image_sends_reference_and_decodes(monkeypatch, frame):
    monkeypatch.setenv("ARK_API_KEY", "k")
    p = ArkImage("seedream", {"key_env": "ARK_API_KEY", "base_url": "https://ark.test/api/v3", "model": "m"})
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"PNGDATA").decode()}]})

    with client(handler) as http:
        data = p.generate(http, ImageRequest("prompt", ratio="9:16", refs=[frame]))
    assert data == b"PNGDATA"
    assert seen["body"]["size"] == "1152x2048"
    assert seen["body"]["image"].startswith("data:image/jpeg;base64,")
    assert seen["body"]["watermark"] is False


def test_kling_jwt_is_signed_with_secret():
    token = jwt_token("ak", "sk", now=1000)
    header, payload, signature = token.split(".")
    assert json.loads(base64.urlsafe_b64decode(_pad(header))) == {"alg": "HS256", "typ": "JWT"}
    assert json.loads(base64.urlsafe_b64decode(_pad(payload))) == {"iss": "ak", "exp": 2800, "nbf": 995}
    expected = base64.urlsafe_b64encode(hmac.new(b"sk", f"{header}.{payload}".encode(), hashlib.sha256).digest())
    assert signature == expected.rstrip(b"=").decode()


def kling(monkeypatch) -> KlingVideo:
    monkeypatch.setenv("KLING_ACCESS_KEY", "ak")
    monkeypatch.setenv("KLING_SECRET_KEY", "sk")
    return KlingVideo("kling", {"key_env": "KLING_ACCESS_KEY", "secret_env": "KLING_SECRET_KEY",
                                "base_url": "https://kling.test", "model": "kling-v3", "mode": "pro"})


def test_kling_image_to_video_with_tail_frame(monkeypatch, frame):
    p = kling(monkeypatch)
    seen = {}

    def handler(req):
        if req.method == "POST":
            assert req.url.path == "/v1/videos/image2video"
            assert req.headers["authorization"].startswith("Bearer ")
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"code": 0, "message": "SUCCEED", "data": {"task_id": "abc"}})
        assert req.url.path == "/v1/videos/image2video/abc"
        return httpx.Response(200, json={"code": 0, "data": {"task_status": "succeed", "task_result": {
            "videos": [{"url": "https://cdn/k.mp4", "duration": "5"}]}}})

    with client(handler) as http:
        task_id = p.submit(http, VideoRequest("p", duration=5, first_frame=frame, last_frame=frame, audio=False))
        status = p.poll(http, task_id)
    body = seen["body"]
    assert task_id == "image2video:abc"
    assert not body["image"].startswith("data:") and base64.b64decode(body["image"])
    assert body["image_tail"] and body["duration"] == "5" and body["sound"] == "off" and body["mode"] == "pro"
    assert status.state == "done" and status.url == "https://cdn/k.mp4"


def test_kling_text_to_video_and_duration_limits(monkeypatch):
    p = kling(monkeypatch)
    endpoint, body = p.build(VideoRequest("p", ratio="9:16"))
    assert endpoint == "text2video" and body["aspect_ratio"] == "9:16" and "image" not in body
    assert p.supports(VideoRequest("p", duration=7)) is not None
    assert p.supports(VideoRequest("p", duration=10)) is None


def test_kling_missing_secret_is_reported(monkeypatch):
    monkeypatch.setenv("KLING_ACCESS_KEY", "ak")
    monkeypatch.delenv("KLING_SECRET_KEY", raising=False)
    p = KlingVideo("kling", {"key_env": "KLING_ACCESS_KEY", "secret_env": "KLING_SECRET_KEY", "model": "kling-v3"})
    assert not p.ready and any("KLING_SECRET_KEY" in m for m in p.missing())


def test_minimax_v2_body_and_refusal(monkeypatch, frame):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    p = MiniMaxVideo("h3", {"key_env": "MINIMAX_API_KEY", "base_url": "https://mm.test", "model": "MiniMax-H3",
                            "resolution": "768P"})
    seen = {}

    def handler(req):
        if req.method == "POST":
            assert req.url.path == "/v2/video_generation"
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"task_id": "42", "base_resp": {"status_code": 0, "status_msg": "success"}})
        assert req.url.path == "/v2/query/video_generation/42"
        return httpx.Response(200, json={"task": {"status": "failed", "error": {
            "code": "1026", "message": "video description contains sensitive content"}}})

    with client(handler) as http:
        task_id = p.submit(http, VideoRequest("p", duration=5, first_frame=frame))
        status = p.poll(http, task_id)
    body = seen["body"]
    assert task_id == "42"
    assert body["model"] == "MiniMax-H3" and body["resolution"] == "768P" and body["ratio"] == "adaptive"
    assert body["content"][1]["role"] == "first_frame"
    assert status.state == "rejected"
    assert p.supports(VideoRequest("p", duration=3)) is not None


def test_minimax_tts_decodes_hex_audio(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    p = MiniMaxTTS("mm", {"key_env": "MINIMAX_API_KEY", "base_url": "https://mm.test", "model": "speech-2.8-hd",
                          "voices": ["v1"]})
    seen = {}

    def handler(req):
        assert req.url.path == "/v1/t2a_v2"
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": {"audio": b"ID3abc".hex()}, "base_resp": {"status_code": 0}})

    with client(handler) as http:
        audio = p.synthesize(http, SpeechRequest("你好", voice="v1", speed=0.9))
    assert audio == b"ID3abc"
    assert seen["body"]["voice_setting"] == {"voice_id": "v1", "speed": 0.9, "vol": 1.0, "pitch": 0}


def test_doubao_tts_joins_stream_chunks(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_ACCESS_KEY", "tok")
    monkeypatch.setenv("DOUBAO_TTS_APP_ID", "app")
    p = DoubaoTTS("doubao", {"key_env": "DOUBAO_TTS_ACCESS_KEY", "app_id_env": "DOUBAO_TTS_APP_ID",
                             "base_url": "https://speech.test/api/v3/tts/unidirectional", "model": "seed-tts-2.0",
                             "voices": ["zh_female_x"]})
    seen = {}
    lines = [json.dumps({"code": 0, "data": base64.b64encode(b"ab").decode()}),
             json.dumps({"code": 0, "data": base64.b64encode(b"cd").decode()}),
             json.dumps({"code": 20000000, "message": "OK", "data": None})]

    def handler(req):
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content="\n".join(lines).encode())

    with client(handler) as http:
        audio = p.synthesize(http, SpeechRequest("测试", voice="zh_female_x", speed=0.9))
    assert audio == b"abcd"
    assert seen["headers"]["x-api-resource-id"] == "seed-tts-2.0" and seen["headers"]["x-api-app-id"] == "app"
    params = seen["body"]["req_params"]
    assert params["speaker"] == "zh_female_x" and params["audio_params"]["speech_rate"] == -10


def test_connection_failures_are_retried_even_without_retries(monkeypatch):
    from ai_video.net import call
    monkeypatch.setattr("ai_video.net.time.sleep", lambda s: None)
    attempts = []

    def handler(req):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
        return httpx.Response(200, json={"ok": True})

    with client(handler) as http:
        assert call(http, "POST", "https://api.test/x", retries=0).json() == {"ok": True}
    assert len(attempts) == 3

    def read_timeout(req):
        raise httpx.ReadTimeout("slow")

    with client(read_timeout) as http, pytest.raises(Exception):
        call(http, "POST", "https://api.test/x", retries=0)  # may have reached the server: no retry


def test_seedance_poll_without_status_is_transient(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    p = ArkVideo("seedance", {"key_env": "ARK_API_KEY", "base_url": "https://ark.test/api/v3", "model": "m"})

    def handler(req):
        return httpx.Response(200, json={"code": 1001, "message": "Task not found"})

    with client(handler) as http, pytest.raises(ProviderError):
        p.poll(http, "t1")


def test_disabled_provider_is_not_ready(monkeypatch):
    p = kling(monkeypatch)
    p.cfg["enabled"] = False
    assert not p.ready and any("enabled" in m for m in p.missing())


PLAN = {"cny_per_afp": 0.002, "daily_afp": 125000, "monthly_afp": 250000}


def ark_video(monkeypatch, **cfg) -> ArkVideo:
    monkeypatch.setenv("ARK_PLAN_API_KEY", "k")
    return ArkVideo("seedance", {"key_env": "ARK_PLAN_API_KEY", "base_url": "https://ark.test/api/plan/v3",
                                 "model": "doubao-seedance-2.0", "plan": "ark", "resolution": "720p", "afp_coef": 230,
                                 **cfg}, PLAN)


def test_seedance_afp_matches_reported_tokens(monkeypatch):
    p = ark_video(monkeypatch)
    req = VideoRequest("p", duration=5, ratio="16:9")
    assert p.video_tokens(req) == 108_900  # 121 frames × 1280 × 720 / 1024, what Ark reports for 5 s at 720p
    cny, afp = p.cost(req)
    assert afp == pytest.approx(108_900 / 10_000 * 230) and cny == pytest.approx(afp * 0.002)
    assert p.settle({"usage": {"completion_tokens": 108_900}}) == pytest.approx((cny, afp))
    assert p.settle({"est_cny": 1.5, "est_afp": 700}) == (1.5, 700.0)  # no usage report: the stored estimate
    mini = ark_video(monkeypatch, resolution="480p", afp_coef=115)
    assert mini.cost(VideoRequest("p", duration=5, ratio="9:16"))[1] == pytest.approx(121 * 496 * 864 / 1024 / 10_000 * 115)
    # Measured on the Agent Plan (2026-10-09), 4 s at 9:16: 2.0 at 720p reported 87,300 tokens, 2.5 at 480p 38,830.
    assert p.video_tokens(VideoRequest("p", duration=4, ratio="9:16")) == 87_300
    v25 = ark_video(monkeypatch, model="doubao-seedance-2.5", resolution="480p", afp_coef=350)
    assert v25.video_tokens(VideoRequest("p", duration=4, ratio="9:16")) == pytest.approx(38_830, abs=1)
    body = p.build_body(VideoRequest("p", duration=6))
    assert body["duration"] == 6 and "frames" not in body


def test_seedance_duration_limits(monkeypatch):
    p = ark_video(monkeypatch)
    assert p.supports(VideoRequest("p", duration=3)) and p.supports(VideoRequest("p", duration=16))
    assert p.supports(VideoRequest("p", duration=4)) is None
    assert ark_video(monkeypatch, max_seconds=30).supports(VideoRequest("p", duration=30)) is None


def test_ark_entry_without_afp_coef_is_not_ready(monkeypatch):
    p = ark_video(monkeypatch, afp_coef=None)
    assert not p.ready and any("afp_coef" in m for m in p.missing())


def test_seedream_afp_charges_extra_refs_only(monkeypatch, frame):
    monkeypatch.setenv("ARK_PLAN_API_KEY", "k")
    p = ArkImage("seedream", {"key_env": "ARK_PLAN_API_KEY", "base_url": "https://x", "model": "m", "plan": "ark",
                              "afp_per_image": 150, "afp_per_extra_ref": 10}, PLAN)
    assert [p.cost(ImageRequest("p", refs=[frame] * n))[1] for n in (0, 1, 3)] == [150, 150, 170]
    assert p.cost(ImageRequest("p"))[0] == pytest.approx(0.3)


def test_seedream_sizes_stay_in_the_1_5k_tier(monkeypatch):
    from ai_video.media import ratio_value
    from ai_video.providers.ark import IMAGE_SIZES
    for ratio, size in IMAGE_SIZES.items():
        w, h = map(int, size.split("x"))
        assert w * h <= 1536 * 1536, ratio
        assert abs(w / h - ratio_value(ratio)) / ratio_value(ratio) < 0.02, ratio
    monkeypatch.setenv("ARK_PLAN_API_KEY", "k")
    p = ArkImage("seedream", {"key_env": "ARK_PLAN_API_KEY", "base_url": "https://x", "model": "m"})
    with client(lambda req: httpx.Response(500)) as http, pytest.raises(ProviderError, match="5:4"):
        p.generate(http, ImageRequest("p", ratio="5:4"))


def test_load_providers_injects_plans_and_rejects_unknown_plan():
    from ai_video.providers import load_providers
    cfg = {"plans": {"ark": PLAN}, "video": {"s": {"adapter": "ark_video", "plan": "ark", "model": "m", "afp_coef": 230}}}
    p = load_providers(cfg)["video"]["s"]
    assert p.plan_id == "ark" and p.afp_to_cny(1000) == pytest.approx(2.0)
    with pytest.raises(ValueError, match="plans"):
        load_providers({"video": {"s": {"adapter": "ark_video", "plan": "nope", "model": "m"}}})


def test_plan_key_prefix_warning(monkeypatch):
    cfg = {"key_env": "MINIMAX_API_KEY", "key_prefix": "sk-cp-", "base_url": "https://mm.test", "model": "MiniMax-H3"}
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-api-123")
    assert MiniMaxVideo("h3", cfg).warnings()
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-cp-123")
    assert MiniMaxVideo("h3", cfg).warnings() == []


def test_minimax_tts_turns_pause_tags_into_its_own_syntax():
    from ai_video.providers.minimax import speech_text
    assert speech_text("霜降<long pause>，秋天<short pause>的最后<sigh>一个节气") == "霜降<#0.8#>，秋天<#0.3#>的最后一个节气"
    assert speech_text("已经是<#0.5#>原生写法") == "已经是<#0.5#>原生写法"


GEMINI = {"key_env": "GEMINI_API_KEY", "base_url": "https://gemini.test/v1beta"}


def test_gemini_tts_sends_style_and_decodes_wav(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    monkeypatch.setattr("ai_video.providers.gemini.media.to_mp3", lambda b: b"MP3:" + b)
    p = GeminiTTS("gemini_tts", {**GEMINI, "model": "gemini-3.8-flash-tts", "voices": ["Charon"]})
    seen = {}

    def handler(req):
        seen["path"], seen["key"] = req.url.path, req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"steps": [{"type": "thought"}, {"type": "model_output", "content": [
            {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(b"WAV").decode()}]}]})

    with client(handler) as http:
        audio = p.synthesize(http, SpeechRequest("霜降到了", voice="Charon", style="沉稳"))
    body = seen["body"]
    assert audio == b"MP3:WAV" and seen["path"] == "/v1beta/interactions" and seen["key"] == "AQ.k"
    text = body["input"][0]["content"][0]
    assert body["input"][0]["type"] == "user_input" and text["text"] == "霜降到了"
    assert text["annotations"] == [{"type": "speech_metadata", "style": "沉稳"}]
    assert body["generation_config"]["speech_config"] == [{"voice": "Charon"}]
    assert body["response_format"]["type"] == "audio"


def test_gemini_image_sends_refs_and_reads_output(monkeypatch, frame):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    p = GeminiImage("nano_banana", {**GEMINI, "model": "gemini-nano-banana-2.1", "image_size": "2K",
                                    "thinking_level": "medium"})
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"output_image": {"data": base64.b64encode(b"IMG").decode()}})

    with client(handler) as http:
        assert p.generate(http, ImageRequest("p", ratio="9:16", refs=[frame])) == b"IMG"
    body = seen["body"]
    assert [part["type"] for part in body["input"]] == ["text", "image"]
    assert body["response_format"] == {"type": "image", "mime_type": "image/png", "aspect_ratio": "9:16",
                                       "image_size": "2K"}
    assert body["generation_config"] == {"thinking_level": "medium"}


def test_gemini_blocked_output_is_rejected(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    p = GeminiImage("nano_banana", {**GEMINI, "model": "m"})

    def handler(req):
        return httpx.Response(200, json={"status": "failed", "steps": [{"type": "model_output", "content": [
            {"type": "text", "text": "blocked by the SAFETY filter"}]}]})

    with client(handler) as http, pytest.raises(Rejected):
        p.generate(http, ImageRequest("p"))


def test_provider_client_uses_its_own_proxy(monkeypatch):
    monkeypatch.setenv("GEMINI_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen = {}
    real = httpx.Client

    def fake_client(**kw):
        seen.clear()
        seen.update(kw)
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    monkeypatch.setattr("ai_video.net.httpx.Client", fake_client)
    gemini = GeminiTTS("g", {**GEMINI, "model": "m", "voices": ["v"], "proxy_env": "GEMINI_PROXY"})
    gemini.client().close()
    assert seen["proxy"] == "http://127.0.0.1:7890" and seen["trust_env"] is False
    MiniMaxTTS("m", {"key_env": "X", "base_url": "https://mm", "model": "m", "voices": ["v"]}).client().close()
    assert "proxy" not in seen
    monkeypatch.delenv("GEMINI_PROXY")
    assert any("GEMINI_PROXY" in w for w in gemini.warnings())
    slow = ArkImage("seedream", {"key_env": "X", "base_url": "https://x", "model": "m", "timeout_seconds": 600})
    slow.client().close()
    assert seen["timeout"].read == 600 and seen["timeout"].connect == 15
    slow.client(timeout=30).close()
    assert seen["timeout"] == 30  # an explicit timeout still wins


def test_billed_calls_are_never_resent_after_they_may_have_been_processed(monkeypatch):
    from ai_video.errors import Unconfirmed
    from ai_video.net import call
    monkeypatch.setattr("ai_video.net.time.sleep", lambda s: None)
    replies = iter([httpx.Response(429, json={"error": {"code": "RateLimit", "message": "slow down"}}),
                    httpx.Response(200, json={"ok": True})])
    with client(lambda req: next(replies)) as http:
        assert call(http, "POST", "https://api.test/x", billed=True).json() == {"ok": True}  # 429: nothing was done

    sent = []

    def gateway_error(req):
        sent.append(1)
        return httpx.Response(503, json={"error": {"code": "ServiceUnavailable", "message": "busy"}})

    with client(gateway_error) as http, pytest.raises(Unconfirmed):
        call(http, "POST", "https://api.test/x", billed=True)
    assert len(sent) == 1

    def read_timeout(req):
        sent.append(1)
        raise httpx.ReadTimeout("slow")

    sent.clear()
    with client(read_timeout) as http, pytest.raises(Unconfirmed):
        call(http, "POST", "https://api.test/x", billed=True)
    assert len(sent) == 1
    sent.clear()
    with client(gateway_error) as http, pytest.raises(ProviderError) as info:
        call(http, "GET", "https://api.test/x", retries=2)  # free calls keep retrying 5xx
    assert len(sent) == 3 and not isinstance(info.value, Unconfirmed)


def minimax_video(monkeypatch, **cfg) -> MiniMaxVideo:
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-cp-k")
    return MiniMaxVideo("h3", {"key_env": "MINIMAX_API_KEY", "base_url": "https://mm.test", "model": "MiniMax-H3",
                               "price_cny_per_second": 0.6, **cfg})


@pytest.mark.parametrize("reply,expected", [
    ({"base_resp": {"status_code": 1002, "status_msg": "rate limit exceeded"}}, ProviderError),
    ({}, ProviderError),
    ({"task": {"status": "mystery"}}, ProviderError),
    ({"task": {"status": "Processing"}}, "pending"),
    ({"task": {"status": "Fail", "error": {"code": "x", "message": "timeout"}}}, "failed"),
    ({"base_resp": {"status_code": 1026, "status_msg": "video description contains sensitive content"}}, "rejected"),
])
def test_h3_poll_only_ends_on_explicit_statuses(monkeypatch, reply, expected):
    p = minimax_video(monkeypatch)
    with client(lambda req: httpx.Response(200, json=reply)) as http:
        if expected is ProviderError:
            with pytest.raises(ProviderError):  # transient: wait_video keeps polling
                p.poll(http, "1")
        else:
            assert p.poll(http, "1").state == expected


def test_h3_cost_follows_the_reported_seconds(monkeypatch):
    p = minimax_video(monkeypatch)
    assert p.settle({"usage": {"total_seconds": 6, "output_seconds": 6}}) == pytest.approx((3.6, 0.0))
    assert p.settle({"usage": {}, "est_cny": 2.4}) == (2.4, 0.0)


def test_minimax_ping_uses_the_plan_remains_endpoint(monkeypatch):
    p = minimax_video(monkeypatch, ping_url="https://www.minimax.test/v1/token_plan/remains")
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers["authorization"]))
        return httpx.Response(200, json={"base_resp": {"status_code": 0},
                                         "model_remains": [{"current_weekly_remaining_percent": 98}]})

    with client(handler) as http:
        assert "98%" in p.ping(http)
    assert seen == [("/v1/token_plan/remains", "Bearer sk-cp-k")]
    with client(lambda req: httpx.Response(200, json={"base_resp": {"status_code": 1004, "status_msg": "auth"}})) as http, \
            pytest.raises(ProviderError):
        p.ping(http)


def test_minimax_tts_estimate_counts_cjk_twice_like_the_bill(monkeypatch):
    from ai_video.providers.minimax import billable_chars, speech_text
    text = "霜降，是秋天的最后一个节气。<short pause>停车坐爱枫林晚，霜叶红于二月花。"
    assert billable_chars(speech_text(text)) == 63  # MiniMax reported usage_characters 63 for this line
    p = MiniMaxTTS("mm", {"key_env": "MINIMAX_API_KEY", "model": "speech-2.8-hd", "voices": ["v"],
                          "price_cny_per_10k_chars": 3.5})
    assert p.estimate(SpeechRequest(text, voice="v")) == pytest.approx(3.5 * 63 / 10_000)


def test_minimax_speech_text_edge_cases():
    from ai_video.providers.minimax import speech_text
    assert speech_text("<long pause>停车<short pause><long pause>坐爱<sigh>枫林晚<long pause>") == "停车<#1.1#>坐爱枫林晚"
    assert speech_text("当 v<c 且 m>0 时") == "当 v<c 且 m>0 时"  # not a tag
    assert speech_text("<medium pause>") == ""


def test_seedream_downloads_the_returned_url(monkeypatch, frame):
    monkeypatch.setenv("ARK_PLAN_API_KEY", "k")
    p = ArkImage("seedream", {"key_env": "ARK_PLAN_API_KEY", "base_url": "https://ark.test/api/plan/v3", "model": "m"})
    seen = {}

    def handler(req):
        if req.method == "POST":
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"data": [{"url": "https://tos.test/x.jpeg", "size": "1152x2048"}]})
        seen["get_auth"] = req.headers.get("authorization")
        return httpx.Response(200, content=b"JPEGDATA")

    with client(handler) as http:
        assert p.generate(http, ImageRequest("p", ratio="9:16", refs=[frame, frame])) == b"JPEGDATA"
    body = seen["body"]
    assert body["response_format"] == "url" and "sequential_image_generation" not in body
    assert isinstance(body["image"], list) and len(body["image"]) == 2 and seen["get_auth"] is None


def test_afp_coefficient_by_resolution_and_low_res_models(monkeypatch):
    p = ark_video(monkeypatch, resolution="1080p", afp_coef={"480p": 230, "720p": 230, "1080p": 255})
    assert p.coef() == 255 and p.ready
    assert p.usage_afp({"completion_tokens": 10_000}) == 255
    fast = ark_video(monkeypatch, model="doubao-seedance-2.0-fast", resolution="1080p", afp_coef=185)
    assert not fast.ready and any("480p" in m for m in fast.missing())
    hyphen = ark_video(monkeypatch, model="doubao-seedance-2-5-260628", resolution="480p", afp_coef=350)
    assert hyphen.video_tokens(VideoRequest("p", duration=4, ratio="9:16")) == pytest.approx(38_830, abs=1)


def test_ping_fails_on_any_auth_style_4xx():
    from ai_video.providers.base import ping_auth
    invalid = httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT",
                                                  "details": [{"reason": "API_KEY_INVALID"}]}})
    with client(lambda req: invalid) as http, pytest.raises(ProviderError, match="API_KEY_INVALID"):
        ping_auth(http, "GET", "https://api.test/models")
    with client(lambda req: httpx.Response(404, json={})) as http:
        assert "404" in ping_auth(http, "GET", "https://api.test/tasks/0")  # no such object: the key was accepted


def test_gemini_errors_keep_their_reason_and_ping_explains_unknown_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.bad")
    body = [{"error": {"code": 401, "message": "Request had invalid authentication credentials.", "status": "UNAUTHENTICATED",
                       "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_TOKEN_TYPE_UNSUPPORTED"}]}}]
    p = GeminiTTS("gemini_tts", {**GEMINI, "model": "m", "voices": ["Charon"]})
    with client(lambda req: httpx.Response(401, json=body)) as http, pytest.raises(ProviderError) as info:
        p.ping(http)
    assert "ACCESS_TOKEN_TYPE_UNSUPPORTED" in str(info.value) and "复制密钥" in str(info.value)
    with client(lambda req: httpx.Response(200, json={"models": []})) as http:
        assert "200" in p.ping(http)


def test_download_retries_a_storage_503(monkeypatch, tmp_path):
    from ai_video.net import download
    monkeypatch.setattr("ai_video.net.time.sleep", lambda s: None)
    replies = iter([httpx.Response(503, text="busy"), httpx.Response(200, content=b"mp4")])
    with client(lambda req: next(replies)) as http:
        download(http, "https://tos.test/x.mp4", tmp_path / "x.mp4")
    assert (tmp_path / "x.mp4").read_bytes() == b"mp4"


def test_generated_image_lost_in_download_is_unconfirmed(monkeypatch):
    from ai_video.errors import Unconfirmed
    monkeypatch.setattr("ai_video.net.time.sleep", lambda s: None)
    monkeypatch.setenv("ARK_PLAN_API_KEY", "k")
    p = ArkImage("seedream", {"key_env": "ARK_PLAN_API_KEY", "base_url": "https://ark.test/api/plan/v3", "model": "m"})

    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://tos.test/gone.jpeg"}]})
        return httpx.Response(403, text="expired")

    with client(handler) as http, pytest.raises(Unconfirmed, match="已扣费"):
        p.generate(http, ImageRequest("p", ratio="9:16"))


def test_minimax_sends_only_the_pronunciations_the_line_uses(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-cp-k")
    p = MiniMaxTTS("mm", {"key_env": "MINIMAX_API_KEY", "base_url": "https://mm.test", "model": "speech-2.8-hd",
                          "voices": ["presenter_male"]})
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": {"audio": "00ff"}, "base_resp": {"status_code": 0}})

    req = SpeechRequest("天姥连天向天横，势拔五岳掩赤城。", voice="presenter_male",
                        pronunciations={"天姥": "(tian1)(mu3)", "訇然": "(hong1)(ran2)"})
    with client(handler) as http:
        p.synthesize(http, req)
    assert seen["body"]["pronunciation_dict"] == {"tone": ["天姥/(tian1)(mu3)"]}
