"""Deterministic preparation of assistant text for natural speech."""

from html import unescape
from html.parser import HTMLParser
import re
import unicodedata


class _TextOnlyHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class SpeechTextNormalizer:
    """Remove visual-only formatting without rewriting substantive content."""

    _FENCED_CODE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
    _MARKDOWN_LINK = re.compile(r"!?(?:\[([^\]]*)\])\([^)]*\)")
    _RAW_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
    _UUID = re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    )
    _SEPARATOR = re.compile(r"^\s*[-_*~=]{3,}\s*$")
    _HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
    _BULLET = re.compile(r"^\s*[-+*]\s+(.+)$")
    _NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")

    def normalize(self, text: str) -> str:
        if not isinstance(text, str):
            raise ValueError("Speech text must be a string.")

        prepared = self._FENCED_CODE.sub("\n", text)
        prepared = self._MARKDOWN_LINK.sub(lambda match: match.group(1) or "", prepared)
        prepared = self._RAW_URL.sub("", prepared)
        prepared = self._UUID.sub("", prepared)
        prepared = self._strip_html(prepared)
        prepared = unescape(prepared)

        paragraphs: list[str] = []
        current: list[str] = []
        for raw_line in prepared.splitlines():
            line = raw_line.strip()
            if not line:
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                continue
            if self._SEPARATOR.match(line):
                continue
            line = re.sub(r"^\s*>+\s?", "", line)
            heading = self._HEADING.match(line)
            bullet = self._BULLET.match(line)
            numbered = self._NUMBERED.match(line)
            if heading:
                line = self._sentence(heading.group(1))
            elif bullet:
                line = self._sentence(bullet.group(1))
            elif numbered:
                line = self._sentence(f"{numbered.group(1)}. {numbered.group(2)}")
            current.append(line)
        if current:
            paragraphs.append(" ".join(current))

        normalized = "\n\n".join(paragraphs)
        normalized = re.sub(
            r"(?<!\w)_{1,3}(.+?)_{1,3}(?!\w)",
            r"\1",
            normalized,
        )
        normalized = re.sub(r"(`+|\*{1,3}|~~)", "", normalized)
        normalized = re.sub(r"[!?]{2,}", lambda match: match.group(0)[0], normalized)
        normalized = re.sub(r"\.{4,}", "...", normalized)
        normalized = re.sub(r"\s*[—–]\s*", ", ", normalized)
        normalized = re.sub(r"\s*;\s*", ". ", normalized)
        normalized = self._remove_decorative_symbols(normalized)
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r" *\n{2,} *", "\n\n", normalized)
        normalized = re.sub(r"\s+([,.!?])", r"\1", normalized)
        normalized = re.sub(r",\s*,+", ", ", normalized)
        normalized = normalized.strip()
        normalized = re.sub(r"^[,.-]+\s*", "", normalized)
        if not normalized:
            raise ValueError("Speech text is empty after normalization.")
        return normalized

    @staticmethod
    def _sentence(value: str) -> str:
        value = value.strip()
        return value if not value or value[-1] in ".!?" else f"{value}."

    @staticmethod
    def _strip_html(value: str) -> str:
        parser = _TextOnlyHTMLParser()
        parser.feed(value)
        parser.close()
        return " ".join(parser.parts)

    @staticmethod
    def _remove_decorative_symbols(value: str) -> str:
        return "".join(
            character
            for character in value
            if unicodedata.category(character) not in {"So", "Sk"}
        )
