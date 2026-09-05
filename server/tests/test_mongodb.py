import unittest
from uuid import uuid4

from app import database


class MongoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_database(attempts=1)

    def setUp(self):
        self.owner = f"test-{uuid4()}"

    def tearDown(self):
        database.articles.delete_many({"owner_id": self.owner})
        database.categories.delete_many({"owner_id": self.owner})

    def test_taxonomy_validation_and_guarded_deletion(self):
        items = database.list_categories(self.owner)
        category, subcategory = database.valid_taxonomy(self.owner, "Work", "AI")
        self.assertEqual((category, subcategory), ("Work", "AI"))
        self.assertEqual(database.valid_taxonomy(self.owner, "Work", "Unknown"), ("Work", ""))
        work = next(item for item in items if item["name"] == "Work")
        self.assertEqual(database.delete_category(self.owner, work["id"]), "not_empty")

    def test_articles_are_scoped_to_their_owner(self):
        values = {
            "url": "https://example.com/article",
            "title": "Article",
            "site": "Example",
            "author": "",
            "published": "",
            "word_count": 2,
            "strategy": "test",
            "category": "Work",
            "subcategory": "AI",
        }
        article_id = database.record_article(
            self.owner, values, "inbox/article.md", "article.md", "# Article", "test"
        )
        self.assertEqual(database.get_article(self.owner, article_id)["markdown"], "# Article")
        self.assertIsNone(database.get_article("another-owner", article_id))


if __name__ == "__main__":
    unittest.main()
