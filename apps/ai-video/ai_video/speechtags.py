"""Speech directions inside narration: word tags in the Gemini style (<short pause>, <sigh>) and
MiniMax pause markers (<#0.5#>). Matched narrowly, so ordinary text that happens to contain '<' and
'>' (a comparison, a formula) is left alone."""
import re

WORD_TAG = re.compile(r"<\s*([A-Za-z][A-Za-z ]{0,30}?)\s*>")
PAUSE_MARK = re.compile(r"<#(\d+(?:\.\d+)?)#>")


def strip_tags(text: str) -> str:
    """Narration as it should read on screen."""
    return " ".join(PAUSE_MARK.sub("", WORD_TAG.sub("", text)).split())
