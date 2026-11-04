"""Question-type comments (lesson 2.1): a sign of unmet demand and a source of video ideas.

Each sentence counts when it ends with a question mark, holds a clear question phrase, or asks for more videos.
Phrases easily confused with statements are left out (Vietnamese "never", "however much money", "let anyone know").
"""

from __future__ import annotations

import re

from yt_niche.core.text import normalize

# Clear question phrases anywhere in the sentence (matched on normalized text). Vietnamese and English.
QUESTION_PHRASES = (
    "tại sao", "vì sao", "làm sao", "làm thế nào", "như thế nào", "là gì", "có ai", "ai biết không", "có nên",
    "được không", "có phải", "cho hỏi", "xin hỏi", "bao giờ thì", "khi nào thì", "ở đâu", "can you", "could you",
    "is there", "does anyone",
)
# Requests for more content: a signal of unmet demand.
REQUEST_PHRASES = (
    "làm video về", "ra video về", "làm thêm video", "làm thêm về", "phần tiếp theo đi", "mong ad làm", "ad làm",
    "please make", "do a video", "make a video", "part 2", "next video",
)
# English question words count only at the start of a sentence ("what a great video" is not a question).
EN_LEADING = ("how", "why", "what", "where", "when", "which", "who", "is", "are", "can", "does", "do")
# Exclamations: "what a great video", "how amazing is this".
EN_EXCLAIM = ("a", "an", "great", "cool", "amazing", "beautiful", "nice", "good", "awesome", "lovely", "sweet")

_NOISE = re.compile(r"https?://\S+")
_SENTENCES = re.compile(r"[^.!?？\n]+[.!?？]*")


def question_sentences(text: str) -> list[str]:
    text = _NOISE.sub(" ", text or "")
    out = []
    for raw in _SENTENCES.findall(text):
        sentence = raw.strip()
        if not sentence:
            continue
        norm = f" {normalize(sentence)} "
        words = norm.split()
        if (
            sentence.endswith(("?", "？"))
            or any(f" {p} " in norm for p in QUESTION_PHRASES + REQUEST_PHRASES)
            or (words and words[0] in EN_LEADING and len(words) >= 4 and words[1] not in EN_EXCLAIM)
        ):
            out.append(sentence)
    return out


def is_question(text: str) -> bool:
    return bool(question_sentences(text))


def question_comments(comments: list[dict], min_chars: int = 15, top: int = 30) -> list[dict]:
    """Keep comments that hold a question, sorted by likes + replies, deduplicated. "question" is the extracted sentence(s)."""
    seen, out = set(), []
    for c in sorted(comments, key=lambda c: c.get("likes", 0) + 2 * c.get("replies", 0), reverse=True):
        text = " ".join((c.get("text") or "").split())
        key = normalize(text)
        if len(text) < min_chars or key in seen:
            continue
        sentences = [s for s in question_sentences(text) if len(normalize(s).split()) >= 3]
        if not sentences:
            continue
        seen.add(key)
        out.append({**c, "text": text, "question": " ".join(sentences)})
    return out[:top]
