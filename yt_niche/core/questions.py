"""Bình luận dạng câu hỏi (bài 2.1): dấu hiệu nhu cầu chưa được đáp ứng và nguồn ý tưởng video.

Xét từng câu: câu kết thúc bằng dấu hỏi, có cụm hỏi rõ nghĩa, hoặc là lời xin làm thêm video.
Bỏ cụm dễ nhầm với câu kể ("đừng bao giờ", "có tiền bao nhiêu", "cho ai biết").
"""

from __future__ import annotations

import re

from yt_niche.core.text import normalize

# Cụm hỏi rõ nghĩa ở bất kỳ vị trí nào trong câu (so trên chữ đã chuẩn hoá).
QUESTION_PHRASES = (
    "tại sao", "vì sao", "làm sao", "làm thế nào", "như thế nào", "là gì", "có ai", "ai biết không", "có nên",
    "được không", "có phải", "cho hỏi", "xin hỏi", "bao giờ thì", "khi nào thì", "ở đâu", "can you", "could you",
    "is there", "does anyone",
)
# Lời xin thêm nội dung: tín hiệu cầu chưa được đáp ứng.
REQUEST_PHRASES = (
    "làm video về", "ra video về", "làm thêm video", "làm thêm về", "phần tiếp theo đi", "mong ad làm", "ad làm",
    "please make", "do a video", "make a video", "part 2", "next video",
)
# Từ hỏi tiếng Anh chỉ tính khi đứng đầu câu ("what a great video" không phải câu hỏi).
EN_LEADING = ("how", "why", "what", "where", "when", "which", "who", "is", "are", "can", "does", "do")
# Câu cảm thán: "what a great video", "how amazing is this".
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
    """Lọc bình luận có câu hỏi, sắp theo lượt thích + trả lời, bỏ trùng. "question" là câu hỏi trích ra."""
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
