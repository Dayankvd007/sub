"""Deterministic orchestration around the model call.

Flow: clean -> remove rolling duplicates -> build context windows -> for each
window: prompt -> provider -> validate -> (bounded corrective retry) ->
(bounded split-on-failure) -> reconstruct Persian cues -> caller renders SRT.

Every expected speech-cue index is either translated exactly once or reported
as failed. Nothing is silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .chunking import Window, build_windows
from .cleaning import clean_cues
from .config import TranslationConfig
from .dedup import remove_rolling_duplicates
from .models import Cue, TranslatedCue
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from .providers import ProviderError, TranslationProvider, TranslationRequest
from .validation import ValidationError, validate_translation


@dataclass
class PipelineStats:
    prompt_version: str = PROMPT_VERSION
    total_cues: int = 0
    speech_cues: int = 0
    non_speech_cues: int = 0
    windows: int = 0
    translated: int = 0
    failed_indices: list[int] = field(default_factory=list)
    provider_calls: int = 0
    corrective_retries: int = 0
    splits: int = 0
    cleaning_changes: list[str] = field(default_factory=list)
    dedup_changes: list[str] = field(default_factory=list)


@dataclass
class TranslationResult:
    translated_cues: list[TranslatedCue]
    stats: PipelineStats

    @property
    def ok(self) -> bool:
        return not self.stats.failed_indices


def _request_for(window: Window, config: TranslationConfig, extra: str | None = None) -> TranslationRequest:
    user_prompt = build_user_prompt(window, title=config.title, glossary=config.glossary or None)
    if extra:
        user_prompt = f"{user_prompt}\n\nCORRECTION: {extra}"
    return TranslationRequest(
        window_id=window.window_id,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        required_indices=window.target_indices,
        source_by_index={c.cue_index: c.english_text for c in window.target_cues},
    )


def _split(window: Window, config: TranslationConfig) -> list[Window]:
    cues = window.target_cues
    mid = len(cues) // 2
    left_target, right_target = cues[:mid], cues[mid:]
    ctx = config.context
    left = Window(
        window_id=f"{window.window_id}a",
        target_cues=left_target,
        context_before=window.context_before,
        context_after=right_target[:ctx],
    )
    right = Window(
        window_id=f"{window.window_id}b",
        target_cues=right_target,
        context_before=left_target[-ctx:],
        context_after=window.context_after,
    )
    return [left, right]


def _translate_window(
    window: Window,
    provider: TranslationProvider,
    config: TranslationConfig,
    stats: PipelineStats,
    depth: int,
) -> dict[int, str]:
    """Translate one window, returning index -> persian for whatever succeeded.

    Indexes that cannot be translated within the bounded retry/split budget are
    recorded in stats.failed_indices.
    """
    correction: str | None = None
    attempts = config.max_retries + 1
    for attempt in range(attempts):
        stats.provider_calls += 1
        try:
            raw = provider.translate(_request_for(window, config, correction))
            validated = validate_translation(raw, window.target_indices)
            return validated.translations
        except ValidationError as exc:
            correction = exc.correction
            if attempt + 1 < attempts:
                stats.corrective_retries += 1
                continue
        except ProviderError:
            # Transport/credential failure is terminal for this run; surface it.
            raise

    # Retries exhausted: split if we can and are within the depth budget.
    if len(window.target_cues) > 1 and depth < config.max_split_depth:
        stats.splits += 1
        merged: dict[int, str] = {}
        for sub in _split(window, config):
            merged.update(_translate_window(sub, provider, config, stats, depth + 1))
        return merged

    # Cannot split further: these indexes fail visibly.
    stats.failed_indices.extend(window.target_indices)
    return {}


def translate_cues(
    cues: list[Cue],
    provider: TranslationProvider,
    config: TranslationConfig | None = None,
) -> TranslationResult:
    config = config or TranslationConfig()
    stats = PipelineStats(total_cues=len(cues))

    cleaned, clean_changes = clean_cues(cues)
    deduped, dedup_changes = remove_rolling_duplicates(cleaned)
    stats.cleaning_changes = clean_changes
    stats.dedup_changes = dedup_changes

    by_index = {c.cue_index: c for c in deduped}
    speech = [c for c in deduped if c.is_speech]
    stats.speech_cues = len(speech)
    stats.non_speech_cues = len(deduped) - len(speech)

    windows = build_windows(
        deduped,
        target_size=config.target_size,
        max_size=config.max_size,
        context=config.context,
    )
    stats.windows = len(windows)

    translations: dict[int, str] = {}
    for window in windows:
        translations.update(_translate_window(window, provider, config, stats, depth=0))

    translated_cues: list[TranslatedCue] = []
    for idx in sorted(translations):
        cue = by_index[idx]
        translated_cues.append(
            TranslatedCue(
                cue_index=cue.cue_index,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                english_text=cue.english_text,
                persian_text=translations[idx],
            )
        )
    stats.translated = len(translated_cues)
    stats.failed_indices = sorted(set(stats.failed_indices))
    return TranslationResult(translated_cues=translated_cues, stats=stats)
