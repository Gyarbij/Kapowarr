import unittest
from asyncio import run
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bs4 import BeautifulSoup

from backend.base.definitions import GCDownloadSource
from backend.base.helpers import normalise_query_string
from backend.implementations import getcomics
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

    @patch('backend.implementations.getcomics.iter_commit')
    @patch('backend.implementations.getcomics.Volume')
    @patch(
        'backend.implementations.getcomics.__purify_link',
        new_callable=AsyncMock
    )
    def test_forced_issue_download_preserves_explicit_issue(
        self,
        purify_link,
        volume,
        iter_commit
    ):
        download_class = MagicMock()
        purify_link.return_value = ('https://example.test/file', download_class)
        iter_commit.side_effect = lambda links: iter(links)
        volume.return_value.get_issue.return_value.get_data.return_value = (
            SimpleNamespace(calculated_issue_number=4.0)
        )
        group = {
            'web_sub_title': 'Special edition',
            'info': {'issue_number': None},
            'links': {GCDownloadSource.GETCOMICS: ['https://example.test']}
        }

        run(getattr(getcomics, '__purify_download_group')(
            group,
            volume_id=1,
            issue_id=44,
            web_link='https://example.test/release',
            web_title='Example release',
            forced_match=True
        ))

        self.assertEqual(
            download_class.call_args.kwargs['covered_issues'],
            4.0
        )


if __name__ == '__main__':
    unittest.main()
