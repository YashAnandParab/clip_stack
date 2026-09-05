import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import auth, database, main


class ArticleFlowTests(unittest.TestCase):
    def setUp(self):
        self.owner = f"test-{uuid4()}"
        self.secret = patch.object(auth, "SESSION_SECRET", b"x" * 48)
        self.secret.start()
        self.addCleanup(self.secret.stop)
        self.headers = {
            "X-Clip-Token": auth.create_token({"sub": self.owner}, 60),
            "X-Clip-Client": "test",
        }

    def tearDown(self):
        database.articles.delete_many({"owner_id": self.owner})
        database.categories.delete_many({"owner_id": self.owner})

    def test_final_markdown_is_in_mongodb_and_the_vault_then_deleted(self):
        html = "<article><h1>A useful article</h1>" + "<p>Useful text for extraction. </p>" * 30 + "</article>"
        with tempfile.TemporaryDirectory() as vault, patch.object(main, "VAULT_PATH", Path(vault)):
            with TestClient(main.app) as client:
                response = client.post(
                    "/clip",
                    headers=self.headers,
                    json={
                        "html": html,
                        "url": "https://example.com/article",
                        "category": "Work",
                        "subcategory": "AI",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                saved = response.json()
                detail = client.get(
                    f"/articles/{saved['article_id']}", headers=self.headers
                ).json()
                exported = Path(vault, saved["path"])
                self.assertEqual(detail["markdown"], exported.read_text(encoding="utf-8"))
                self.assertEqual(
                    client.delete(
                        f"/articles/{saved['article_id']}", headers=self.headers
                    ).status_code,
                    200,
                )
                self.assertFalse(exported.exists())


if __name__ == "__main__":
    unittest.main()
