"""Jinja2：prompts/ 下的提示词（原样保留空白）和 templates/ 下的 Markdown 模板（块标签独占一行）。"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
_prompts = Environment(loader=FileSystemLoader(HERE / "prompts"), undefined=StrictUndefined,
                       keep_trailing_newline=True)
_templates = Environment(loader=FileSystemLoader(HERE / "templates"), undefined=StrictUndefined,
                         trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)


def prompt(name: str, **ctx) -> str:
    return _prompts.get_template(name).render(**ctx).strip() + "\n"


def template(name: str, **ctx) -> str:
    return _templates.get_template(name).render(**ctx)
