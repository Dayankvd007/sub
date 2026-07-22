"""Phase 1 CLI: caption file -> Persian SRT.

Runs the full pipeline without Chrome or FastAPI. Defaults to the online
Anthropic provider; use `--provider mock` for an offline, no-cost dry run that
still exercises loading, cleaning, dedup, windowing, validation, and SRT
generation end to end.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_MODEL, TranslationConfig
from .loaders import LoaderError, load_cues
from .pipeline import translate_cues
from .providers import AnthropicProvider, MockProvider, ProviderError, TranslationProvider
from .srt import to_srt


def _build_provider(name: str, model: str) -> TranslationProvider:
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        return AnthropicProvider(model=model)
    raise ValueError(f"Unknown provider: {name!r}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subtitle-translate",
        description="Translate English subtitle cues (JSON3/VTT/SRT/Phase-0 fixture) into a Persian SRT.",
    )
    parser.add_argument("input", help="Path to the caption file (.json/.json3/.vtt/.srt).")
    parser.add_argument("-o", "--output", help="Output SRT path (default: stdout).")
    parser.add_argument(
        "--provider",
        default="anthropic",
        choices=["anthropic", "mock"],
        help="Translation provider (default: anthropic). 'mock' runs offline with no API key.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model id (default: {DEFAULT_MODEL}).")
    parser.add_argument("--title", help="Optional video title passed to the model as context.")
    parser.add_argument("--window-size", type=int, default=50, help="Target cues per window (default: 50).")
    parser.add_argument("--max-window-size", type=int, default=70, help="Max cues per window (default: 70).")
    parser.add_argument("--context", type=int, default=2, help="Read-only context cues per side (default: 2).")
    parser.add_argument("--no-rtl-wrap", action="store_true", help="Do not wrap Persian lines in RTL marks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        cues = load_cues(args.input)
    except (LoaderError, FileNotFoundError, OSError) as exc:
        print(f"error: could not load input: {exc}", file=sys.stderr)
        return 2

    config = TranslationConfig(
        provider_name=args.provider,
        model=args.model,
        target_size=args.window_size,
        max_size=args.max_window_size,
        context=args.context,
        title=args.title,
    )

    try:
        provider = _build_provider(args.provider, args.model)
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        result = translate_cues(cues, provider, config)
    except ProviderError as exc:
        print(f"error: provider failed: {exc}", file=sys.stderr)
        return 3

    srt_text = to_srt(result.translated_cues, rtl_wrap=not args.no_rtl_wrap)
    if args.output:
        Path(args.output).write_text(srt_text, encoding="utf-8")

    s = result.stats
    print(
        f"[stats] prompt={s.prompt_version} cues={s.total_cues} speech={s.speech_cues} "
        f"non_speech={s.non_speech_cues} windows={s.windows} translated={s.translated} "
        f"failed={len(s.failed_indices)} calls={s.provider_calls} retries={s.corrective_retries} "
        f"splits={s.splits}",
        file=sys.stderr,
    )
    if s.failed_indices:
        print(f"[stats] FAILED cue indexes: {s.failed_indices}", file=sys.stderr)

    if not args.output:
        sys.stdout.write(srt_text)

    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
