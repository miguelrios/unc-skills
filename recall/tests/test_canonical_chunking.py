from __future__ import annotations

import sys
import unittest
from pathlib import Path

RECALL = Path(__file__).resolve().parents[1]
SERVER = RECALL / "server"
sys.path.insert(0, str(RECALL))
sys.path.insert(0, str(SERVER))

from recall_server.canonical import (  # noqa: E402
    MAX_CANONICAL_CHUNK_BYTES,
    canonical_text_chunks,
)


class CanonicalChunkingTests(unittest.TestCase):
    def test_short_text_remains_one_chunk(self) -> None:
        self.assertEqual(canonical_text_chunks("small document"), ["small document"])

    def test_large_unicode_text_is_lossless_and_bounded(self) -> None:
        text = ("alpha βeta 🧠\n" * 3_000) + ("z" * 1_050_000)

        chunks = canonical_text_chunks(text)

        self.assertGreater(len(text.encode("utf-8")), 1_000_000)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(
            all(
                0 < len(chunk.encode("utf-8")) <= MAX_CANONICAL_CHUNK_BYTES
                for chunk in chunks
            )
        )

    def test_empty_text_has_one_stable_chunk(self) -> None:
        self.assertEqual(canonical_text_chunks(""), [""])


if __name__ == "__main__":
    unittest.main()
