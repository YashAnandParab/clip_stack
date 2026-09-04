import unittest

from app.extraction import extract


class TableExtractionTests(unittest.TestCase):
    def test_table2md_replaces_table_in_place(self) -> None:
        html = """
        <html>
          <head><title>Fund comparison</title></head>
          <body>
            <article>
              <h1>Fund comparison</h1>
              <p>Text before the table explains what the comparison contains.</p>
              <table>
                <caption>Returns</caption>
                <tr><th>Fund</th><th>Return</th></tr>
                <tr>
                  <td rowspan="2"><a href="/fund">Alpha</a></td>
                  <td>10%</td>
                </tr>
                <tr><td>12%</td></tr>
              </table>
              <p>Text after the table summarizes the comparison results.</p>
            </article>
          </body>
        </html>
        """

        result = extract(html, "https://example.com/article")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("**Returns**", result.markdown)
        self.assertIn("| Fund | Return |", result.markdown)
        self.assertIn("| --- | --- |", result.markdown)
        self.assertIn("[Alpha](https://example.com/fund)", result.markdown)
        self.assertNotIn("CLIPSTACKTABLE", result.markdown)
        self.assertLess(
            result.markdown.index("Text before"), result.markdown.index("| Fund")
        )
        self.assertLess(
            result.markdown.index("| Fund"), result.markdown.index("Text after")
        )


if __name__ == "__main__":
    unittest.main()
