"""Compare authenticated TTS and Realtime deployments without saving audio."""

import argparse
import hashlib
import time
import urllib.request


def request_audio(base_url: str, token: str, conversation: int, message: int):
    url = (
        f"{base_url.rstrip('/')}/api/v1/conversations/{conversation}"
        f"/messages/{message}/speech"
    )
    request = urllib.request.Request(
        url,
        data=b'{"response_format":"mp3"}',
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=90) as response:
        content = response.read()
        return {
            "milliseconds": round((time.monotonic() - started) * 1000),
            "bytes": len(content),
            "media_type": response.headers.get_content_type(),
            "sha256": hashlib.sha256(content).hexdigest(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Call the existing authenticated message-speech endpoint on one "
            "TTS and one Realtime-configured local backend. No audio is saved."
        )
    )
    parser.add_argument("--tts-url", required=True)
    parser.add_argument("--realtime-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--conversation-id", type=int, required=True)
    parser.add_argument("--message-id", type=int, required=True)
    args = parser.parse_args()
    for engine, url in (("tts", args.tts_url), ("realtime", args.realtime_url)):
        result = request_audio(
            url, args.token, args.conversation_id, args.message_id
        )
        print(engine, result)


if __name__ == "__main__":
    main()
