"""The exact English copy used by every publication path.

Translation and semantic language review belong to the calling agent or author.
The CLI binds that review to the text, rather than treating Latin text as English.
"""
from dataclasses import asdict, dataclass
import hashlib
from html import unescape
import json
import re
import unicodedata

from .errors import CourierError


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def prose(text):
    """Exclude code and link destinations, but retain readable labels/alt text."""
    text = re.sub(r"(?ms)^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[ \t]*$", "", text)
    text = re.sub(r"(`+)[^\n]*?\1", "", text)
    text = re.sub(r"(?is)<(?:pre|code)\b[^>]*>.*?</(?:pre|code)>", "", text)
    text = re.sub(r"https?://[^\s<>\"']+", "", text)
    labels = re.findall(r"""(?:alt|title)\s*=\s*["']([^"']*)["']""", text, re.I)
    text = re.sub(r"<[^>]*>", "", text)
    return unescape(text + " " + " ".join(labels))


def check_copy(fields):
    for name, text in fields.items():
        if not text.strip():
            raise CourierError("publication_text_missing", f"Provide English {name} before publishing.")
        visible = prose(text) if name in {"body", "changelog"} else text
        if any(char.isalpha() and "LATIN" not in unicodedata.name(char, "") for char in visible):
            raise CourierError(
                "publication_translation_required",
                f"Translate {name} into English (transliterate names when needed). "
                "Keep literal commands/code in code spans or fenced blocks."
            )
    if len(fields.get("summary", "")) > 256:
        raise CourierError("project_metadata", "The English project summary exceeds 256 characters.")


@dataclass(frozen=True)
class Publication:
    title: str
    summary: str
    body: str
    changelog: str

    @classmethod
    def read(cls, config):
        return cls(
            config.project.get("title", ""), config.project.get("summary", ""),
            config.text("project", "body"), config.text("release", "changelog"),
        )

    @property
    def sha256(self):
        return digest({"language": "en", **asdict(self)})

    @property
    def page(self):
        return {"title": self.title, "summary": self.summary, "body": self.body}

    @property
    def page_sha256(self):
        return digest(self.page)

    def release_name(self, artifact):
        return f"{self.title} {artifact.key}"

    def validate(self, config, *, require_review=True):
        settings = config.raw.get("publication", {})
        if settings.get("language", "en") != "en":
            raise CourierError("publication_language", "Website publications must use English (publication.language: en).")
        check_copy(asdict(self))
        if require_review and settings.get("reviewed_sha256") != self.sha256:
            raise CourierError(
                "publication_review_required",
                "The English publication text is new or changed. Translate and read the title, summary, "
                "body and changelog, then run review-language --language en. "
                "That command records your review; it does not translate or detect English."
            )
        return self

    def preview(self, config):
        try:
            self.validate(config)
            issue = None
        except CourierError as exc:
            issue = exc.as_dict()
        return {"language": "en", "sha256": self.sha256, "reviewed": issue is None,
                "fields": asdict(self), **({"issue": issue} if issue else {})}


def reviewed_publication(config):
    return Publication.read(config).validate(config)
