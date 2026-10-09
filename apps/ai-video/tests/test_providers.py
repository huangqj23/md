import base64
import hashlib
import hmac
import json

import httpx
import pytest
from PIL import Image

from ai_video.errors import Rejected, classify
from ai_video.providers.ark import ArkImage, ArkVideo
from ai_video.providers.base import ImageRequest, SpeechRequest, VideoRequest
from ai_video.providers.doubao_tts import DoubaoTTS
from ai_video.providers.kling import KlingVideo, jwt_token
from ai_video.providers.minimax import MiniMaxTTS, MiniMaxVideo
from ai_video.providers.relay302 import Relay302TTS, Relay302Video, model_names

RELAY = {"key_env": "RELAY_API_KEY", "base_url_env": "RELAY_BASE_URL", "base_url": "https://api.302.ai"}


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
    assert seen["body"]["size"] == "1440x2560"
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


def test_relay302_unified_video_body_and_poll(monkeypatch, frame):
    monkeypatch.setenv("RELAY_API_KEY", "rk")
    monkeypatch.setenv("RELAY_BASE_URL", "https://api.302ai.com/")
    p = Relay302Video("wan27_302", {**RELAY, "model": "wan2.7", "resolution": "720P",
                                    "models_by_mode": {"t2v": "wan2.7-t2v", "i2v": "wan2.7-i2v"},
                                    "audio_field": "generate_audio"})
    seen = {}

    def handler(req):
        assert req.headers["authorization"] == "Bearer rk"
        if req.method == "POST":
            assert str(req.url) == "https://api.302ai.com/302/v2/video/create"
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"task_id": "301", "status": "pending", "created_at": "x"})
        assert req.url.path == "/302/v2/video/fetch/301"
        return httpx.Response(200, json={"task_id": "301", "status": "completed", "video_url": "https://file.302.ai/v.mp4"})

    with client(handler) as http:
        task_id = p.submit(http, VideoRequest("p", duration=5, first_frame=frame, last_frame=frame, audio=False))
        status = p.poll(http, task_id)
    body = seen["body"]
    assert body["model"] == "wan2.7-i2v" and body["duration"] == 5 and body["resolution"] == "720P"
    assert body["image"].startswith("data:image/jpeg;base64,") and body["end_image"].startswith("data:image/jpeg")
    assert "aspect_ratio" not in body and body["generate_audio"] is False
    assert status.state == "done" and status.url == "https://file.302.ai/v.mp4"
    t2v = p.build_body(VideoRequest("p", ratio="9:16"))
    assert t2v["model"] == "wan2.7-t2v" and t2v["aspect_ratio"] == "9:16"


def test_relay302_supports_checks_duration_last_frame_and_mode(monkeypatch, frame):
    monkeypatch.setenv("RELAY_API_KEY", "rk")
    p = Relay302Video("happyhorse_302", {**RELAY, "model": "happyhorse-1.0", "end_image": False,
                                         "durations": [3, 15], "models_by_mode": {"i2v": "happyhorse-1.0-i2v"}})
    assert p.supports(VideoRequest("p", duration=5, first_frame=frame)) is None
    assert "尾帧" in p.supports(VideoRequest("p", duration=5, first_frame=frame, last_frame=frame))
    assert "文生视频" in p.supports(VideoRequest("p", duration=5))
    assert "3–15" in p.supports(VideoRequest("p", duration=20, first_frame=frame))


def test_disabled_provider_is_not_ready(monkeypatch):
    monkeypatch.setenv("RELAY_API_KEY", "rk")
    p = Relay302Video("happyhorse_302", {**RELAY, "model": "happyhorse-1.0", "enabled": False})
    assert not p.ready and any("enabled" in m for m in p.missing())


def test_relay302_failed_task_with_moderation_message_is_rejected(monkeypatch):
    monkeypatch.setenv("RELAY_API_KEY", "rk")
    p = Relay302Video("seedance_302", {**RELAY, "model": "m"})

    def handler(req):
        return httpx.Response(200, json={"status": "failed", "raw_response": '{"error":{"code":"InputImageSensitiveContentDetected"}}'})

    with client(handler) as http:
        assert p.poll(http, "1").state == "rejected"


def test_relay_reuses_native_adapters_with_path_prefix(monkeypatch, frame):
    monkeypatch.setenv("RELAY_API_KEY", "rk")
    monkeypatch.delenv("RELAY_BASE_URL", raising=False)
    seedance = ArkVideo("seedance_302", {**RELAY, "base_path": "/volcengine/api/v3", "model": "doubao-seedance-2-0-260128"})
    kling = KlingVideo("kling_302", {**RELAY, "base_path": "/klingai", "model": "kling-v3", "durations": [3, 15]})
    h3 = MiniMaxVideo("h3_302", {**RELAY, "base_path": "/minimaxi", "model": "MiniMax-H3"})
    seedream = ArkImage("seedream_302", {**RELAY, "base_path": "/doubao", "model": "doubao-seedream-5-0-pro-260628"})
    assert kling.ready and kling.headers() == {"Authorization": "Bearer rk"}
    paths = []

    def handler(req):
        paths.append(req.url.path)
        assert req.headers["authorization"] == "Bearer rk"
        if req.url.path.startswith("/volcengine"):
            return httpx.Response(200, json={"id": "s1"})
        if req.url.path.startswith("/klingai"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "k1"}})
        if req.url.path.startswith("/minimaxi"):
            return httpx.Response(200, json={"task_id": "m1"})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"IMG").decode()}]})

    with client(handler) as http:
        assert seedance.submit(http, VideoRequest("p", first_frame=frame)) == "s1"
        kling.submit(http, VideoRequest("p", first_frame=frame))
        h3.submit(http, VideoRequest("p", first_frame=frame))
        seedream.generate(http, ImageRequest("p"))
    assert paths == ["/volcengine/api/v3/contents/generations/tasks", "/klingai/v1/videos/image2video",
                     "/minimaxi/v2/video_generation", "/doubao/images/generations"]
    assert kling.supports(VideoRequest("p", duration=7)) is None
    assert kling.supports(VideoRequest("p", duration=20)) is not None


def test_relay302_tts_sends_provider_and_model_then_downloads(monkeypatch):
    monkeypatch.setenv("RELAY_API_KEY", "rk")
    p = Relay302TTS("minimax_tts_302", {**RELAY, "provider": "minimaxi", "model": "speech-2.8-hd", "voices": ["v1"]})
    doubao = Relay302TTS("doubao_tts_302", {**RELAY, "provider": "doubao", "model": "", "voices": ["zh_v"]})
    assert p.ready and doubao.ready
    seen = []

    def handler(req):
        if req.url.path == "/302/tts/generate":
            seen.append(json.loads(req.content))
            return httpx.Response(200, json={"audio_url": "https://file.302.ai/a.mp3", "data": {}})
        assert str(req.url) == "https://file.302.ai/a.mp3"
        return httpx.Response(200, content=b"MP3")

    with client(handler) as http:
        assert p.synthesize(http, SpeechRequest("你好", voice="v1", speed=0.9)) == b"MP3"
        assert doubao.synthesize(http, SpeechRequest("你好", voice="zh_v")) == b"MP3"
    assert seen[0] == {"text": "你好", "provider": "minimaxi", "voice": "v1", "speed": 0.9, "output_format": "mp3",
                       "model": "speech-2.8-hd"}
    assert "model" not in seen[1] and seen[1]["provider"] == "doubao"


def test_model_names_handles_unknown_listing_layouts():
    listing = {"data": [{"model": "doubao-seedance-2-0", "params": {"duration": [5, 10]}},
                        {"name": "kling-v3", "features": ["i2v"]}], "extra": ["MiniMax-H3"]}
    assert model_names(listing) == ["doubao-seedance-2-0", "kling-v3", "MiniMax-H3"]
    keyed = {"code": 0, "data": {"kling-v3": {"name_cn": "可灵"}, "MiniMax-H3": {"durations": [6, 10]}}}
    assert model_names(keyed) == ["kling-v3", "MiniMax-H3"]


def test_openai_image_async_mode_polls_until_ready(monkeypatch):
    from ai_video.providers.openai_image import OpenAIImage
    monkeypatch.setenv("RELAY_API_KEY", "rk")
    monkeypatch.delenv("RELAY_BASE_URL", raising=False)
    monkeypatch.setattr("ai_video.providers.relay_async.time.sleep", lambda s: None)
    p = OpenAIImage("gpt_image_302", {**RELAY, "base_path": "/v1", "model": "gpt-image-2", "async": True})
    polls = []

    def handler(req):
        if req.method == "POST":
            assert req.url.path == "/v1/images/generations" and req.url.params["async"] == "true"
            return httpx.Response(200, json={"task_id": "g1"})
        assert req.url.path == "/async_result" and req.url.params["task_id"] == "g1"
        polls.append(1)
        if len(polls) == 1:
            return httpx.Response(200, json={"content_type": "", "data": "", "err": "result pending", "status_code": 0})
        body = json.dumps({"data": [{"b64_json": base64.b64encode(b"GPTIMG").decode()}]})
        return httpx.Response(200, json={"content_type": "application/json", "data": body, "err": "", "status_code": 200})

    with client(handler) as http:
        assert p.generate(http, ImageRequest("p")) == b"GPTIMG"
    assert len(polls) == 2


def test_chat_streams_answer_and_skips_reasoning():
    from ai_video.llm import chat
    events = [
        {"choices": [{"delta": {"reasoning_content": "思考中"}}]},
        {"choices": [{"delta": {"content": "枯藤"}}]},
        {"choices": [{"delta": {"content": "老树"}}]},
    ]
    sse = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"

    def handler(req):
        body = json.loads(req.content)
        assert body["stream"] is True and req.url.path == "/v1/chat/completions"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    with client(handler) as http:
        assert chat(http, "https://api.302.ai/v1", "rk", "deepseek-v4-pro", "p") == "枯藤老树"


def test_seedream_async_mode_and_upstream_refusal(monkeypatch, frame):
    monkeypatch.setenv("RELAY_API_KEY", "rk")
    monkeypatch.delenv("RELAY_BASE_URL", raising=False)
    monkeypatch.setattr("ai_video.providers.relay_async.time.sleep", lambda s: None)
    p = ArkImage("seedream_302", {**RELAY, "base_path": "/doubao", "model": "m", "async": True})
    replies = iter([
        {"content_type": "", "data": "", "err": "result pending", "status_code": 0},
        {"content_type": "application/json", "err": "", "status_code": 200,
         "data": json.dumps({"data": [{"b64_json": base64.b64encode(b"SEED").decode()}]})},
        {"content_type": "application/json", "err": "", "status_code": 400,
         "data": json.dumps({"error": {"code": "OutputImageSensitiveContentDetected", "message": "x"}})},
    ])

    def handler(req):
        if req.method == "POST":
            assert req.url.path == "/doubao/images/generations" and req.url.params["async"] == "true"
            assert json.loads(req.content)["image"].startswith("data:image/jpeg")
            return httpx.Response(200, json={"task_id": "s1"})
        assert req.url.path == "/async_result"
        return httpx.Response(200, json=next(replies))

    with client(handler) as http:
        assert p.generate(http, ImageRequest("p", refs=[frame])) == b"SEED"
        with pytest.raises(Rejected):
            p.generate(http, ImageRequest("p", refs=[frame]))


def test_relay_async_accepts_a_synchronous_answer(monkeypatch):
    from ai_video.providers.relay_async import relay_async
    payload = {"data": [{"b64_json": base64.b64encode(b"SYNC").decode()}]}

    def handler(req):
        assert req.url.params["async"] == "true"
        return httpx.Response(200, json=payload)

    with client(handler) as http:
        assert relay_async(http, "POST", "https://api.302.ai/doubao/images/generations", root="https://api.302.ai",
                           headers={}, json={}) == payload


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
        assert call(http, "POST", "https://api.302.ai/x", retries=0).json() == {"ok": True}
    assert len(attempts) == 3

    def read_timeout(req):
        raise httpx.ReadTimeout("slow")

    with client(read_timeout) as http, pytest.raises(Exception):
        call(http, "POST", "https://api.302.ai/x", retries=0)  # may have reached the server: no retry
