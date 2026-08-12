from __future__ import annotations

from dataclasses import dataclass

from app.services.ingestion.parser import ParsedSection


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    project_id: str
    heading: str
    heading_level: int
    content: str
    page_or_slide: int | None
    char_offset: int
    token_estimate: int

    @property
    def metadata(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "project_id": self.project_id,
            "heading": self.heading,
            "heading_level": self.heading_level,
            "page_or_slide": self.page_or_slide or 0,
            "char_offset": self.char_offset,
        }


def heading_aware_chunk(
    sections: list[ParsedSection],
    doc_id: str,
    project_id: str,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[Chunk]:
    """Split parsed sections into overlapping chunks, never breaking mid-heading-section
    unless the section exceeds max_tokens."""
    chunks: list[Chunk] = []
    idx = 0

    for section in sections:
        text = section.content.strip()
        if not text:
            continue

        est_tokens = _estimate_tokens(text)

        if est_tokens <= max_tokens:
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}_chunk_{idx}",
                    doc_id=doc_id,
                    project_id=project_id,
                    heading=section.heading,
                    heading_level=section.level,
                    content=text,
                    page_or_slide=section.page_or_slide,
                    char_offset=0,
                    token_estimate=est_tokens,
                )
            )
            idx += 1
        else:
            sub_chunks = _split_long_text(text, max_tokens, overlap_tokens)
            for offset, sub in sub_chunks:
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc_id}_chunk_{idx}",
                        doc_id=doc_id,
                        project_id=project_id,
                        heading=section.heading,
                        heading_level=section.level,
                        content=sub,
                        page_or_slide=section.page_or_slide,
                        char_offset=offset,
                        token_estimate=_estimate_tokens(sub),
                    )
                )
                idx += 1

    return chunks


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _split_long_text(
    text: str, max_tokens: int, overlap_tokens: int
) -> list[tuple[int, str]]:
    """Split on sentence boundaries with overlap. Returns (char_offset, text) pairs."""
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4
    sentences = _split_sentences(text)

    results: list[tuple[int, str]] = []
    current_parts: list[str] = []
    current_len = 0
    char_pos = 0
    chunk_start = 0

    for sent in sentences:
        sent_len = len(sent)
        if current_len + sent_len > max_chars and current_parts:
            chunk_text = " ".join(current_parts)
            results.append((chunk_start, chunk_text))

            overlap_acc = 0
            keep: list[str] = []
            for s in reversed(current_parts):
                overlap_acc += len(s)
                keep.insert(0, s)
                if overlap_acc >= overlap_chars:
                    break
            current_parts = keep
            current_len = sum(len(s) for s in current_parts)
            chunk_start = char_pos - current_len

        current_parts.append(sent)
        current_len += sent_len
        char_pos += sent_len

    if current_parts:
        results.append((chunk_start, " ".join(current_parts)))

    return results


def _split_sentences(text: str) -> list[str]:
    import re
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p for p in parts if p.strip()]