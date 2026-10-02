from datetime import date

from ai_daily import vram, write
from ai_daily.models import Event, Item, canonical_url, pick_primary
from ai_daily.textutil import compile_keywords, display_width, first_sentences, matches_keywords
from ai_daily.triage import Layout
from ai_daily.verify import missing_numbers, quote_found


def test_canonical_url_drops_tracking_and_unifies_x():
    assert canonical_url("https://www.example.com/post/?utm_source=x&id=3#top") == "https://example.com/post?id=3"
    assert canonical_url("http://twitter.com/OpenAI/status/123?s=20&t=abc") == "https://x.com/OpenAI/status/123"
    assert Item("s", "S", "official", "t", "https://x.com/a/status/1").id == \
        Item("s", "S", "official", "t", "https://mobile.twitter.com/a/status/1?s=46").id


def test_keywords_short_words_match_whole_words_only():
    kw = compile_keywords(["ai", "agent", "gpu", "大模型"])
    assert matches_keywords("New AI-generated maths", kw)
    assert not matches_keywords("He said nothing", kw)
    assert matches_keywords("Coding agents are here", kw)          # 长词按词首匹配
    assert not matches_keywords("Testing WebGPU layouts", kw)       # gpu 前面紧挨字母，不算
    assert matches_keywords("国产大模型发布", kw)


def test_vram_estimate_qwen25_7b():
    est = vram.estimate({"parameters": {"BF16": 7615616512}, "total": 7615616512})
    assert round(est["gb"]["BF16"], 1) == 15.2
    text = vram.describe(est, context=32768, license_="apache-2.0")
    assert "参数 7.6B" in text
    assert "16 GB 卡：INT8 权重放得下" in text and "24 GB 卡：BF16 权重放得下" in text
    assert "上下文 32,768" in text and "许可证 apache-2.0" in text
    assert vram.describe(vram.estimate({})) == ""


def test_vram_huge_model_does_not_fit():
    est = vram.estimate({"parameters": {"F8_E4M3": 671_000_000_000}, "total": 671_000_000_000})
    text = vram.describe(est)
    assert "16 GB 卡：都放不下" in text and "24 GB 卡：都放不下" in text
    assert "官方权重为 F8_E4M3" in text


def test_vram_refuses_packed_integer_weights():
    # MiMo-V2.6-Pro-RL 这类仓库以 U8 打包存量化权重，元素数不等于参数数
    text = vram.describe(vram.estimate({"parameters": {"U8": 1_024_200_000_000, "BF16": 5_000_000}}))
    assert "U8" in text and "算不出" in text and "GB" not in text


SOURCE = ("GPT-6.1 Sol delivers near-Astra intelligence for a fifth of the price. "
          "It supports a 1,050,000 token context window and costs $2 per million input tokens.")


def test_quote_found_tolerates_whitespace_and_quotes_but_not_paraphrase():
    assert quote_found("It supports a 1,050,000 token   context window", SOURCE)
    assert quote_found("GPT-6.1 Sol delivers near-Astra intelligence for a fifth of the price", SOURCE)
    assert not quote_found("GPT-6.1 Sol is ten times cheaper than every other model on the market", SOURCE)
    assert not quote_found("", SOURCE)


def test_missing_numbers_flags_invented_numbers_only():
    assert missing_numbers("上下文 1,050,000 token，输入每百万 $2，2026 年发布", SOURCE) == []
    assert missing_numbers("价格降了 80%", SOURCE) == ["80"]


def test_missing_numbers_compares_magnitudes_across_languages():
    src = ("OpenAI is raising at least $30 billion at a roughly $1.4 trillion valuation. ChatGPT reaches "
           "1.2 billion weekly users, and ARR is reportedly $65B+. It supports 128K context.")
    assert missing_numbers("拟募资 300 亿美元，估值约 1.4 万亿美元；周活 12 亿，年化收入 650 亿美元，上下文 12.8 万", src) == []
    assert missing_numbers("拟募资 3000 亿美元，周活 12 亿人", src) == ["3000"]         # 数量级不对照样标出来


def test_missing_numbers_reads_english_month_names():
    src = "Reddit will end RSS support on November 13 and close its public API in 2027."
    assert missing_numbers("RSS 支持 11 月 13 日终止，公共 API 2027 年关闭", src) == []
    assert missing_numbers("RSS 支持 12 月 13 日终止", src) == ["12"]                    # 月份对不上照样标出来


def test_check_event_accepts_numbers_from_source_titles():
    from ai_daily.verify import check_event
    release = Item("gh:huggingface/transformers", "huggingface/transformers", "release",
                   "huggingface/transformers v5.18.0", "https://github.com/huggingface/transformers/releases/tag/v5.18.0")
    ev = Event(title="Transformers 5.18", items=[release], track="llm", score=50, label="代码")
    ev.source_text, ev.paragraphs = "New model: a speaker diarization pipeline.", ["Transformers 5.18 新增说话人分离模型。"]
    check_event(ev)
    assert ev.flags == []


def test_untranslated_english_paragraph_is_flagged():
    from ai_daily.verify import check_event, untranslated
    stray = ("For moderators who have relied on RSS channels for their own alerts, Reddit is now recommending a shift "
             "to the Discord Relay Devvit app.")
    mixed = "NVIDIA 发布 NeMo Relay，用于观测 Agent 的模型与工具执行路径：Hermes Agent 已原生集成，把 session、turn 表示为 scope 层级。"
    assert untranslated(stray) and not untranslated(mixed)
    ev = Event(title="Reddit", items=[Item("s", "S", "media", "t", "https://e.com/t")], track="other", score=50,
               label="媒体")
    ev.source_text, ev.paragraphs = stray, ["Reddit 宣布终止 RSS 支持。", stray]
    check_event(ev)
    assert len(ev.flags) == 1 and ev.flags[0].startswith("有一段没翻译成中文（“For moderators")


def test_clean_readme_strips_logo_html_and_badges():
    from ai_daily.collect.hf import clean_readme
    md = ('---\nlicense: mit\n---\n<p align="center"> <img src="logo.png" width="400"/> </p>\n'
          '<p align="center"> 🤗 <a href="https://hf.co/x">HuggingFace</a>&nbsp;&nbsp;| &nbsp;&nbsp;</p>\n'
          '# Qwen-Image-2.1\n![demo](a.png)\nSee [the blog](https://qwen.ai/blog) for details.\n')
    assert clean_readme(md) == "🤗 HuggingFace |\n# Qwen-Image-2.1\nSee the blog for details."


def test_first_sentences():
    text = "第一句。第二句很长很长。第三句。"
    assert first_sentences(text, 12) == "第一句。第二句很长很长。"
    assert first_sentences("One. Two three. Four.", 10) == "One."


def test_check_event_collapses_multiline_quotes():
    from ai_daily.models import Event, Item
    from ai_daily.verify import check_event
    ev = Event(title="t", items=[Item("s", "S", "model", "t", "https://h.co/x")], track="llm", score=1, label="官方")
    ev.source_text = "# Model\nA 7B instruct model."
    ev.paragraphs = ["一个 7B 模型。"]
    ev.quotes = [{"claim": "c", "source_quote": "# Model\nA 7B instruct model."}]
    check_event(ev)
    assert ev.quotes[0]["source_quote"] == "# Model A 7B instruct model."   # 换行会截断 Markdown 引用块
    assert ev.flags == []


def test_editor_titles_are_not_cut_and_the_first_one_that_fits_leads():
    long = "AI 早报 09.30｜OpenAI 发布 GPT-6.1 Sol，旗舰 GPT-6.1 Astra 因为安全评测未通过推迟发布"
    fits = "AI 早报 09.30｜GPT-6.1 Sol 输入每百万 token 2 美元"
    assert display_width("AI 早报") == 3.5 and display_width(fits) == 25

    class Editor:
        def json(self, system, user, **kw):
            return {"highlights": ["看点"], "titles": [long, fits], "cover_lines": ["封面"]}

    ev = Event(title="t", items=[Item("s", "S", "official", "t", "https://e.com/t")], track="llm", score=90, label="官方")
    ev.headline, ev.paragraphs = "中文标题", ["正文。"]
    out = write.write_editor(Editor(), date(2026, 9, 30), Layout(ev, [], [], []))
    assert out["titles"] == [fits, long]                        # 超长的不截断，排到后面


def test_primary_source_follows_the_event_title_then_heat():
    """选题把 DeepSeek 当天的几个新仓库并成“开源昇腾版 DeepEP 与 DeepGEMM”：主来源不能是摘要更长的内部小仓库。"""
    def repo(name, desc, stars):
        return Item("gh-orgs", "GitHub · deepseek-ai", "repo", f"deepseek-ai/{name}：{desc}",
                    f"https://github.com/deepseek-ai/{name}", summary=f"deepseek-ai 新建的仓库，{stars} star，JavaScript",
                    label="官方", meta={"stars": stars})
    kit = repo("dsh-libreoffice-kit", "Office to PDF for Node.js", 2)
    ep = repo("DeepEP-Ascend", "communication library for Ascend", 147)
    gemm = repo("DeepGEMM-Ascend", "GEMM kernels for Ascend", 256)
    tweet = Item("ainews", "X @eliebakouch", "social", "DeepSeek now trained on Ascend", "https://x.com/e/status/1")
    assert pick_primary([kit, ep, gemm, tweet], "DeepSeek 开源昇腾版 DeepEP 与 DeepGEMM") is gemm   # 名字都对上，star 多的
    assert pick_primary([kit, ep, tweet], "DeepSeek 开源昇腾版 DeepEP") is ep
    assert pick_primary([tweet, kit]) is kit                    # 可信度仍然最先比


def test_newsletter_lead_to_the_companys_own_site_counts_as_official():
    blog = Item("tldr-ai", "blog.cloudflare.com", "newsletter", "Clef: open decision models",
                "https://blog.cloudflare.com/clef-decision-models/", meta={"via": "TLDR AI"})
    release = Item("gh:ollama/ollama", "ollama/ollama", "release", "ollama/ollama v0.35.1-rc1",
                   "https://github.com/ollama/ollama/releases/tag/v0.35.1-rc1", summary="models: add clef support")
    assert blog.label == "媒体" and release.label == "代码"
    assert pick_primary([release, blog], "Cloudflare 开源决策模型 Clef 与 RL 微调平台") is blog
    assert pick_primary([release, blog], "Ollama 支持 Clef") is release        # 链到的不是标题里那家公司


def test_candidates_that_rename_an_earlier_release_are_tagged():
    from datetime import datetime, timezone

    from ai_daily.triage import candidate_line, earlier_matches
    since = datetime(2026, 9, 30, 23, 5, tzinfo=timezone.utc)
    before = [(datetime(2026, 9, 29, 15, 30, tzinfo=timezone.utc), "hf-blog",
               "NVIDIA Kumo Tabular Sets a New Accuracy-Efficiency Frontier for Tabular Prediction"),
              (datetime(2026, 9, 30, 21, 15, tzinfo=timezone.utc), "hf-trending", "Cloudflare/Clef decision model"),
              (datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc), "the-decoder", "Agents need an operating system")]

    def item(title, kind="media"):
        return Item("s", "S", kind, title, "https://e.com/" + title[:8])

    items = [item("NVIDIA Releases Kumo Tabular: Open Tabular Foundation Models That Predict New Rows"),
             item("Cloudflare Releases Clef: Open Decision Models"),        # 窗口前 2 小时才出现：今天的新闻
             item("Brian Chesky: AI agents need their own operating system"),   # 普通词重合不算名字
             item("NVIDIA releases a new open model for robotics")]
    hits = earlier_matches(items, before, since)
    assert "hf-blog" in hits[0] and "Kumo Tabular" in hits[0]
    assert hits[1:] == ["", "", ""]
    line = candidate_line(0, items[0], hits[0])
    assert line.endswith(f"｜ 疑似旧闻：{hits[0]}")


def test_missing_numbers_matches_wan_against_plain_source_numbers():
    src = "Security startup Glow Security found more than 13,000 images at 343 organizations."
    assert missing_numbers("超过 1.3 万张截图，涉及 343 家组织", src) == []
    assert missing_numbers("超过 2.6 万张截图", src) == ["2.6"]                  # 对不上的照样标出来
