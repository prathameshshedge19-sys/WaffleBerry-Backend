"""Developer CLI for validating and managing the global Sarvam dictionary."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
if str(BACKEND_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIRECTORY))

from app.config import get_settings
from app.services.ai.exceptions import AIServiceError
from app.services.ai.sarvam_pronunciation_provider import (
    SarvamPronunciationProvider,
)
from app.services.pronunciation_dictionary_service import (
    PronunciationDictionarySourceLoader,
    PronunciationDictionaryValidationError,
)


def parser():
    commands = argparse.ArgumentParser(
        description="Manage WaffleBerry's Sarvam pronunciation dictionary."
    )
    subcommands = commands.add_subparsers(dest="command", required=True)
    for name in ("validate", "create"):
        command = subcommands.add_parser(name)
        command.add_argument("--source", required=True)
    update = subcommands.add_parser("update")
    update.add_argument("--source", required=True)
    update.add_argument("--dictionary-id", required=True)
    subcommands.add_parser("list")
    get = subcommands.add_parser("get")
    get.add_argument("--dictionary-id", required=True)
    return commands


async def run(args):
    loaded = None
    if args.command in {"validate", "create", "update"}:
        loaded = PronunciationDictionarySourceLoader().load(args.source)
        print(
            f"Dictionary valid: version={loaded.version}, "
            f"languages={len(loaded.pronunciations)}, entries={loaded.entry_count}."
        )
        if args.command == "validate":
            return
    settings = get_settings()
    provider = SarvamPronunciationProvider(
        api_key=settings.sarvam_api_key or "",
        timeout_seconds=settings.sarvam_timeout_seconds,
    )
    if args.command == "create":
        identifier = await provider.create(loaded.provider_payload())
        print("Dictionary created successfully.")
        print(f"Dictionary ID: {identifier}")
        print("Add it manually to SARVAM_PRONUNCIATION_DICTIONARY_ID in .env.")
    elif args.command == "update":
        identifier = await provider.update(
            args.dictionary_id, loaded.provider_payload()
        )
        print("Dictionary updated successfully.")
        print(f"Dictionary ID: {identifier}")
    elif args.command == "list":
        result = await provider.list()
        print(json.dumps(result, indent=2))
    elif args.command == "get":
        result = await provider.get(args.dictionary_id)
        pronunciations = result["pronunciations"]
        entry_count = sum(len(entries) for entries in pronunciations.values())
        print(
            f"Dictionary found: languages={len(pronunciations)}, "
            f"entries={entry_count}."
        )


def main():
    try:
        asyncio.run(run(parser().parse_args()))
    except (PronunciationDictionaryValidationError, AIServiceError) as exc:
        print(f"Dictionary operation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
