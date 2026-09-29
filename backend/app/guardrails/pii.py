"""Role-aware PII detection and redaction with Microsoft Presidio.

Policy:
* ALWAYS_MASKED entities (card numbers, national IDs, bank accounts...) are masked for everyone,
  including in user queries before they reach the LLM or traces.
* Employee PII (personal emails, phone numbers, employee IDs, salaries, dates of birth) is
  masked for every role except HR and C-level. RBAC already keeps HR records away from other
  roles; this is defense in depth against anything that slips through (e.g. PII embedded in a
  report, or pasted into the conversation).
* PERSON names are intentionally not masked: reports legitimately name people, and spaCy's
  small model produces too many false positives to make masking names useful.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from app.rbac.policy import PII_PRIVILEGED_ROLES, Role

log = logging.getLogger(__name__)

ALWAYS_MASKED: frozenset[str] = frozenset(
    {
        "CREDIT_CARD",
        "US_SSN",
        "US_BANK_NUMBER",
        "US_PASSPORT",
        "US_ITIN",
        "IBAN_CODE",
        "CRYPTO",
        "IN_PAN",
        "IN_AADHAAR",
        "UK_NHS",
    }
)
EMPLOYEE_PII: frozenset[str] = frozenset(
    {"EMAIL_ADDRESS", "PHONE_NUMBER", "EMPLOYEE_ID", "SALARY", "DATE_OF_BIRTH"}
)
SCORE_THRESHOLD = 0.4

# Shared inboxes (security@, hr@, api-support@) are not personal data.
_PERSONAL_EMAIL = re.compile(r"^[A-Za-z]+[._][A-Za-z]+@", re.ASCII)


def _custom_recognizers() -> list[PatternRecognizer]:
    money = r"(?:₹|Rs\.?\s?|INR\s?|\$|USD\s?)?\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|(?:₹|Rs\.?\s?|INR\s?|\$)?\b\d{5,8}(?:\.\d{1,2})?\b"
    return [
        PatternRecognizer(
            supported_entity="EMPLOYEE_ID",
            patterns=[Pattern("finsolve_employee_id", r"\bFINEMP\d{4}\b", 0.9)],
        ),
        # Low base score: a number only counts as a salary when salary-like context is nearby.
        PatternRecognizer(
            supported_entity="SALARY",
            patterns=[Pattern("salary_amount", money, 0.05)],
            context=[
                "salary",
                "salaries",
                "ctc",
                "compensation",
                "pay",
                "paid",
                "earns",
                "earning",
                "package",
            ],
        ),
        PatternRecognizer(
            supported_entity="DATE_OF_BIRTH",
            patterns=[
                Pattern("iso_date", r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b", 0.05),
                Pattern("dmy_date", r"\b\d{1,2}[/-]\d{1,2}[/-](?:19|20)?\d{2}\b", 0.05),
                Pattern(
                    "long_date",
                    r"\b(?:\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
                    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?)"
                    r"\s+(?:19|20)\d{2}\b",
                    0.05,
                ),
            ],
            # "bear" because Presidio matches on spaCy lemmas and "born" lemmatizes to it.
            context=["birth", "born", "bear", "dob", "birthday"],
        ),
    ]


@dataclass
class RedactionResult:
    text: str
    entities: list[str] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return bool(self.entities)


def _use_offline_suffix_list() -> None:
    # Presidio's email recognizer calls tldextract, which otherwise downloads the public suffix
    # list on first use; the bundled snapshot keeps redaction free of runtime network calls.
    import tldextract.tldextract as tld

    tld.TLD_EXTRACTOR = tld.TLDExtract(suffix_list_urls=(), cache_dir=None)


class PIIGuard:
    def __init__(self) -> None:
        _use_offline_suffix_list()
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
        )
        self.analyzer = AnalyzerEngine(
            nlp_engine=provider.create_engine(), supported_languages=["en"]
        )
        for rec in _custom_recognizers():
            self.analyzer.registry.add_recognizer(rec)
        self.anonymizer = AnonymizerEngine()

    @staticmethod
    def entities_for(role: Role | str | None) -> frozenset[str]:
        if role is not None and Role(role) in PII_PRIVILEGED_ROLES:
            return ALWAYS_MASKED
        return ALWAYS_MASKED | EMPLOYEE_PII

    def analyze(self, text: str, entities: frozenset[str]) -> list[RecognizerResult]:
        if not text.strip():
            return []
        results = self.analyzer.analyze(
            text=text, language="en", entities=sorted(entities), score_threshold=SCORE_THRESHOLD
        )
        return [
            r
            for r in results
            if not (
                r.entity_type == "EMAIL_ADDRESS"
                and not _PERSONAL_EMAIL.match(text[r.start : r.end])
            )
        ]

    def redact(
        self, text: str, role: Role | str | None, context_prefix: str = ""
    ) -> RedactionResult:
        """Redact `text` for `role`. `context_prefix` is preceding text used only as detection
        context (e.g. the previous streamed sentence) and is not returned."""
        entities = self.entities_for(role)
        full = context_prefix + text
        offset = len(context_prefix)
        results = [r for r in self.analyze(full, entities) if r.end > offset]
        if not results:
            return RedactionResult(text)
        # Clip matches that started inside the prefix so they apply to `text` only.
        clipped = [
            RecognizerResult(r.entity_type, max(r.start, offset) - offset, r.end - offset, r.score)
            for r in results
        ]
        operators = {
            e: OperatorConfig("replace", {"new_value": f"[REDACTED_{e}]"})
            for e in {r.entity_type for r in clipped}
        }
        out = self.anonymizer.anonymize(text=text, analyzer_results=clipped, operators=operators)  # type: ignore[arg-type]
        return RedactionResult(out.text, sorted({r.entity_type for r in clipped}))

    def redact_query(self, text: str) -> RedactionResult:
        """Strip high-risk identifiers from user input before it reaches the LLM / traces."""
        return self.redact(text, role=Role.C_LEVEL)


class StreamingRedactor:
    """Buffers streamed tokens and releases them sentence-by-sentence after redaction, so PII
    never reaches the client even mid-stream."""

    _BOUNDARY = re.compile(r"[.!?:;]\s|\n")

    def __init__(self, guard: PIIGuard, role: Role | str, min_chars: int = 40) -> None:
        self.guard = guard
        self.role = role
        self.min_chars = min_chars
        self._buf = ""
        self._tail = ""
        self.entities: set[str] = set()

    def _emit(self, segment: str) -> str:
        res = self.guard.redact(segment, self.role, context_prefix=self._tail)
        self.entities.update(res.entities)
        self._tail = (self._tail + segment)[-80:]
        return res.text

    def push(self, token: str) -> str:
        self._buf += token
        if len(self._buf) < self.min_chars:
            return ""
        cut = -1
        for m in self._BOUNDARY.finditer(self._buf):
            cut = m.end()
        if cut <= 0:
            return ""
        segment, self._buf = self._buf[:cut], self._buf[cut:]
        return self._emit(segment)

    def flush(self) -> str:
        segment, self._buf = self._buf, ""
        return self._emit(segment) if segment else ""


@lru_cache
def get_pii_guard() -> PIIGuard:
    return PIIGuard()
