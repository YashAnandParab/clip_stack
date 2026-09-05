import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth
from app.main import app


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.secret = patch.object(auth, "SESSION_SECRET", b"x" * 48)
        self.secret.start()
        self.addCleanup(self.secret.stop)

    def test_signed_token_round_trip_and_tamper_rejection(self):
        token = auth.create_token({"sub": "owner-1", "email": "a@example.com"}, 60)
        self.assertEqual(auth.read_token(token)["sub"], "owner-1")
        self.assertIsNone(auth.read_token(token + "tampered"))

    def test_protected_routes_require_a_user(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/articles").status_code, 401)
            token = auth.create_token({"sub": "owner-1"}, 60)
            self.assertEqual(
                client.get("/categories", headers={"X-Clip-Token": token}).status_code,
                200,
            )

    @patch.object(auth.id_token, "verify_oauth2_token")
    def test_google_identity_is_reduced_to_verified_profile(self, verify):
        verify.return_value = {
            "sub": "owner-1",
            "email": "a@example.com",
            "email_verified": True,
            "name": "A User",
        }
        with patch.object(auth, "GOOGLE_CLIENT_ID", "client-id"):
            self.assertEqual(auth.verify_google("credential")["sub"], "owner-1")


if __name__ == "__main__":
    unittest.main()
