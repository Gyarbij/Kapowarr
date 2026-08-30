import pyperf

from backend.base.definitions import (IssueData, SearchResultData,
                                      SpecialVersion, VolumeData)
from backend.base.file_extraction import extract_filename_data
from backend.implementations import matching
from backend.implementations.matching import check_search_result_match
from backend.implementations.weekly_packs import parse_weekly_pack

FILENAMES = (
    'Iron-Man Volume 2 Issue 3.cbr',
    'Batman (1940) Volume 2 Issue 11-25.zip',
    'Tales of the Teen Titans v2 (1984) Issue 51-58.cbr',
    'Doctor Strange, Sorcerer Supreme Volume 2 Issues #4.0-4.5 (03-2022)',
    'Infinity Gauntlet #1 - 6 (1991-1992)',
    'Batman 026-050 (1945-1949)/Batman 048 (08-1948).cbr',
    'Batman Annual (1961) Volume 1 Issue 10.cbz',
    'The Amazing Spider-Man (2022) Volume 06 Issue 065.Deaths.cbz',
    'Venom (2021) #0021 [2023-08-01] [cv-996034].cbz',
    'Blacksad 6.1 - They All Fall Down Part 1.cbz',
)

VOLUME = VolumeData(
    id=1,
    comicvine_id=100,
    title='Lobo',
    alt_title=None,
    year=2025,
    volume_number=1,
    description='',
    site_url='https://example.test/lobo',
    publisher='DC Comics',
    monitored=True,
    monitor_new_issues=True,
    root_folder=1,
    folder='/library/Lobo',
    custom_folder=False,
    special_version=SpecialVersion.NORMAL,
    special_version_locked=False,
    last_cv_fetch=0
)
ISSUES = [
    IssueData(
        id=number,
        volume_id=1,
        comicvine_id=1000 + number,
        issue_number=str(number),
        calculated_issue_number=float(number),
        title=None,
        date='2026-01-01',
        description='',
        monitored=True,
        files=[]
    )
    for number in range(1, 21)
]
NUMBER_TO_YEAR = {
    issue.calculated_issue_number: 2026
    for issue in ISSUES
}
SEARCH_RESULTS = [
    SearchResultData(
        series='Lobo',
        year=2026,
        volume_number=None,
        special_version=None,
        issue_number=float(number),
        annual=False,
        link=f'https://getcomics.org/lobo-{number}-2026/',
        display_title=f'Lobo #{number} (2026)',
        source='GetComics'
    )
    for number in range(1, 21)
]

WEEKLY_POST = {
    'id': 404065,
    'date': '2026-08-19T18:28:22',
    'modified': '2026-08-20T08:00:00',
    'link': 'https://getcomics.org/other-comics/2026-08-19-weekly-pack/',
    'title': {'rendered': '2026.08.19 Weekly Pack'},
    'content': {'rendered': ''.join((
        '<h3>DC COMICS</h3><ul>',
        *(
            '<li>Example Series #{number} : '
            '<a href="https://getcomics.org/dc/example-{number}/">'
            'Download</a></li>'.format(number=number)
            for number in range(1, 41)
        ),
        '</ul>'
    ))}
}


def benchmark_file_extraction() -> None:
    for filename in FILENAMES:
        extract_filename_data(filename)


def benchmark_search_matching() -> None:
    for result in SEARCH_RESULTS:
        check_search_result_match(
            result,
            VOLUME,
            ISSUES,
            NUMBER_TO_YEAR
        )


def benchmark_weekly_pack_parsing() -> None:
    parse_weekly_pack(WEEKLY_POST)


def main() -> None:
    matching.blocklist_contains = lambda _link: None
    runner = pyperf.Runner()
    runner.bench_func('filename_extraction_10', benchmark_file_extraction)
    runner.bench_func('search_matching_20', benchmark_search_matching)
    runner.bench_func('weekly_pack_parsing_40', benchmark_weekly_pack_parsing)


if __name__ == '__main__':
    main()