import unittest

from bs4 import BeautifulSoup

from backend.base.helpers import normalise_query_string
from backend.implementations.getcomics import _get_articles, _get_max_page
from backend.implementations.matching import match_title


class GetComicsParsingTest(unittest.TestCase):
    def test_get_max_page_ignores_navigation_labels(self):
        soup = BeautifulSoup("""
            <nav>
                <span class="page-numbers current">1</span>
                <a class="page-numbers">2</a>
                <a class="page-numbers">1,234</a>
                <a class="next page-numbers">Next</a>
            </nav>
        """, 'html.parser')

        self.assertEqual(_get_max_page(soup), 1234)

    def test_get_articles_skips_entries_without_links(self):
        soup = BeautifulSoup("""
            <article class="post">
                <h1 class="post-title">Missing link</h1>
            </article>
            <article class="post">
                <h1 class="post-title">
                    <a href="https://getcomics.org/example/">Example #1</a>
                </h1>
            </article>
        """, 'html.parser')

        self.assertEqual(
            _get_articles(soup),
            [('https://getcomics.org/example/', 'Example #1')]
        )

    def test_query_and_title_normalisation_match(self):
        self.assertEqual(
            normalise_query_string('Beyonce\u0301 Æsir – Œuvre'),
            'Beyonce aesir - oeuvre'
        )
        self.assertTrue(match_title('Pokemon – Classics', 'Pokémon Classics'))
        self.assertFalse(match_title('Pokemon Classics', 'Pokemon Adventures'))


if __name__ == '__main__':
    unittest.main()
