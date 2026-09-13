import importlib.util
import sys
import tempfile
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


oauth = _load_module(
    "lxns_oauth_test", ROOT / "src" / "plugins" / "chunithm_b30" / "oauth.py"
)
sync = _load_module(
    "maimai_qr_sync_test", ROOT / "src" / "plugins" / "maimai_b50" / "sync.py"
)


class _Level(Enum):
    MASTER = 3


class _SongType(Enum):
    DX = "dx"


class _FC(Enum):
    APP = 0


class _FS(Enum):
    FSDP = 4


class LxnsOAuthTests(unittest.TestCase):
    def test_repository_uses_the_public_client_id(self):
        self.assertEqual(
            oauth.PUBLIC_CLIENT_ID, "9a6be364-2c97-4773-89ec-8c7d04fcbd41"
        )

    def test_authorization_url_requests_write_player_with_pkce(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = Path(directory) / "pending.json"
            with patch.object(oauth, "PENDING_FILE", pending):
                url = oauth.create_authorization_url("123", "client-id")

        query = parse_qs(urlparse(url).query)
        self.assertEqual(
            query["scope"], ["read_user_profile read_player write_player"]
        )
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(query["code_challenge"][0])


class MaimaiQrPayloadTests(unittest.TestCase):
    def test_qr_validation_does_not_accept_urls_or_short_values(self):
        with self.assertRaises(sync.MaimaiQrSyncError):
            sync.normalize_qr_string("https://example.com/SGWCMAIDabc")
        with self.assertRaises(sync.MaimaiQrSyncError):
            sync.normalize_qr_string("SGWCMAIDabc")

    def test_score_is_converted_to_lxns_shape(self):
        score = SimpleNamespace(
            id=1234,
            level_index=_Level.MASTER,
            type=_SongType.DX,
            achievements=100.1234,
            fc=_FC.APP,
            fs=_FS.FSDP,
            dx_score=3012,
        )
        self.assertEqual(
            sync.score_to_lxns_payload(score),
            {
                "id": 1234,
                "type": "dx",
                "level_index": 3,
                "achievements": 100.1234,
                "fc": "app",
                "fs": "fsdp",
                "dx_score": 3012,
            },
        )


class MaimaiQrPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_fetches_and_uploads_without_persisting_qr(self):
        score = SimpleNamespace(
            id=7,
            level_index=_Level.MASTER,
            type=_SongType.DX,
            achievements=99.5,
            fc=None,
            fs=None,
            dx_score=2500,
        )
        qr = "SGWCMAID" + "A" * 40
        fetch = AsyncMock(return_value=[score])
        upload = AsyncMock(return_value=1)
        with patch.object(sync, "_fetch_arcade_scores", fetch), patch.object(
            sync, "_upload_scores", upload
        ):
            result = await sync.sync_maimai_qrcode_to_lxns(qr, "Bearer token")

        self.assertEqual(result, sync.MaimaiQrSyncResult(fetched=1, uploaded=1))
        fetch.assert_awaited_once_with(qr, None)
        upload.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
