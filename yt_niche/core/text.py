"""Simple tokenizer for video titles and keywords (Vietnamese counts syllables)."""

from __future__ import annotations

import re
import unicodedata

# Languages written without spaces between words.
NO_SPACE_LANGUAGES = ("ja", "zh", "zh-Hant", "zh-Hans", "th", "ko")

STOPWORDS = {
    # Vietnamese
    "và", "của", "là", "có", "cho", "với", "những", "các", "một", "này", "đó", "khi", "thì", "mà", "ở", "trong",
    "ra", "vào", "để", "được", "không", "bị", "đã", "sẽ", "rất", "nhất", "gì", "nào", "như", "thế", "từ", "về",
    "lại", "cũng", "hay", "hơn", "đến", "nên", "phải", "tại", "theo", "nhiều", "ai", "ta", "mình", "chúng", "họ",
    "nó", "đây", "kia", "còn", "vẫn", "đang", "luôn", "chỉ", "mới", "cả", "hết", "lắm", "thật", "sự",
    # English
    "the", "a", "an", "of", "to", "in", "for", "and", "or", "is", "are", "on", "with", "how", "why", "what",
    "you", "your", "my", "i", "this", "that", "it", "at", "by", "from", "be", "do", "does", "we", "our", "they",
    "vs", "about", "into", "can", "will", "when", "who", "me",
    # Filler words common in titles (Vietnamese "episode", "part", "new", "most" included)
    "shorts", "short", "video", "tập", "phần", "part", "ep", "official", "full", "hd", "4k", "mới", "nhất", "p1", "p2",
}

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower()
    text = re.sub(r"#\S+", " ", text)  # drop hashtags
    return " ".join(_TOKEN.findall(text))


def tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if not t.isdigit()]


def no_space(language: str) -> bool:
    return language in NO_SPACE_LANGUAGES or language.split("-")[0] in NO_SPACE_LANGUAGES


def word_count(text: str, language: str) -> int:
    """Word count (syllables for Vietnamese). Languages written without spaces return the character count."""
    if no_space(language):
        return len(normalize(text).replace(" ", ""))
    return len(tokens(text))


def contains_all(text: str, phrase: str, language: str) -> bool:
    """The title holds every word of the phrase (any order). Languages without spaces: substring match."""
    if no_space(language):
        return normalize(phrase).replace(" ", "") in normalize(text).replace(" ", "")
    words = set(tokens(text))
    return all(t in words for t in tokens(phrase))


def contains_phrase(text: str, phrase: str) -> bool:
    """The title holds the exact contiguous phrase."""
    return f" {normalize(phrase)} " in f" {normalize(text)} "
