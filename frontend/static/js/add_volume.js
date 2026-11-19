const SearchEls = {
	pre_build: {
		search_entry: document.querySelector('.pre-build-els .search-entry')
	},
	search_bar: {
		bar: document.querySelector('.search-bar'),
		cancel: document.querySelector('#search-cancel-button'),
		input: document.querySelector('#search-input')
	},
	search_results: document.querySelector('#search-results'),
	msgs: {
		blocked: document.querySelector('#search-blocked'),
		failed: document.querySelector('#search-failed'),
		empty: document.querySelector('#search-empty'),
		explain: document.querySelector('#search-explain'),
		loading: document.querySelector('#search-loading')
	},
	filters: {
		translations: document.querySelector('#filter-translations'),
		publisher: document.querySelector('#filter-publisher'),
		volume_number: document.querySelector('#filter-volume-number'),
		year: document.querySelector('#filter-year'),
		issue_count: document.querySelector('#filter-issue-count')
	}
};

let searchRequestId = 0;

function searchPublisherIssues(query, apiKey, requestId) {
	const section = document.getElementById('publisher-issue-results');
	const list = document.getElementById('publisher-issue-list');
	const message = document.getElementById('publisher-issue-message');
	const more = document.getElementById('publisher-issue-more');
	section.classList.remove('hidden');
	list.replaceChildren();
	more.classList.add('hidden');
	message.textContent = 'Searching publisher and library issues...';

	fetchAPI('/releases/search', apiKey, {
		query: encodeURIComponent(query),
		per_page: 12
	})
	.then(json => {
		if (requestId !== searchRequestId) return;
		const result = json.result;
		list.replaceChildren(...result.items.map(issue => IssueCatalog.card(issue)));
		message.textContent = result.total
			? `${result.total} matching ${result.total === 1 ? 'issue' : 'issues'}, including publisher listings not yet linked to the library.`
			: 'No matching issues in the current release window. Series search results are shown below.';
		more.href = `${url_base}/publishers?view=issues&period=all&query=${encodeURIComponent(query)}`;
		more.classList.toggle('hidden', result.total === 0);
	})
	.catch(error => {
		if (requestId !== searchRequestId) return;
		console.error('Publisher issue search failed:', error);
		message.textContent = 'Publisher issue search failed. Try again or refresh release discovery under System > Tasks.';
	});
}

//
// Searching
//
function addAlreadyAdded(entry, id) {
	entry.onclick = e => window.location.href = `${url_base}/volumes/${id}`;

	const title = entry.querySelector('h2');
	const aa_icon = document.createElement('img');
	aa_icon.src = `${url_base}/static/img/check_circle.svg`;
	aa_icon.alt = 'Volume is already added';
	title.appendChild(aa_icon);
};

function buildResults(results) {
	SearchEls.search_results
        .querySelectorAll('button:not(.filter-bar)')
        .forEach(e => e.remove());
	results.forEach(result => {
		const entry = SearchEls.pre_build.search_entry.cloneNode(true);
		entry.dataset.title =
		entry.ariaLabel =
			result.year !== null
            ? `${result.title} (${result.year})`
            : result.title;
		entry.dataset.cover = result.cover_link;
		entry.dataset.comicvine_id = result.comicvine_id;
		entry.dataset._translated = result.translated;
		entry.dataset._title = result.title;
		entry.dataset._year = result.year || '';
		entry.dataset._volume_number = result.volume_number;
		entry.dataset._publisher = result.publisher || '';
		entry.dataset._issue_count = result.issue_count;

		// Only allow adding volume if it isn't already added
		if (result.already_added === null)
			entry.onclick = () => AddVolumeWindow.open({
				volume: result,
				onAdded: id => addAlreadyAdded(entry, id)
			});

		entry.querySelector('img').src = result.cover_link;

		const title = entry.querySelector('h2');
		title.innerText = result.title;

		if (result.year !== null) {
			const year = document.createElement("span");
			year.innerText = ` (${result.year})`;
			title.appendChild(year);
		};

		if (result.already_added !== null)
			addAlreadyAdded(entry, result.already_added);

		const tags = entry.querySelector('.entry-tags');

		if (result.volume_number !== null) {
			const volume_number = document.createElement('p');
			volume_number.innerText = `Volume ${result.volume_number}`;
			tags.appendChild(volume_number);
		};

		const publisher = document.createElement('p');
		publisher.innerText = result.publisher || 'Publisher Unknown';
		tags.appendChild(publisher);

		const issue_count = document.createElement('p');
		issue_count.innerText = `${result.issue_count} issues`;
		tags.appendChild(issue_count);

		const info_link = document.createElement('a');
		info_link.href = result.site_url;
		info_link.innerText = 'Link';
		info_link.onclick = e => e.stopImmediatePropagation();
		tags.appendChild(info_link);

		if (result.aliases.length) {
			const aliases = entry.querySelector('.entry-aliases');
			result.aliases.forEach(alias_text => {
				const alias = document.createElement('p');
				alias.innerText = alias_text;
				aliases.appendChild(alias);
			});
		};

		entry.querySelectorAll('.entry-description, .entry-spare-description')
            .forEach(
                d => d.innerHTML = result.description
            );

		SearchEls.search_results.appendChild(entry);
	});

	// Fill filters
	const years = new Set(results.map(r => r.year).sort());
	SearchEls.filters.year.innerHTML = '';
	const all_years_option = document.createElement('option');
	all_years_option.value = '';
	all_years_option.innerText = 'All Years';
	all_years_option.selected = true;
	SearchEls.filters.year.appendChild(all_years_option);

	years.forEach(y => {
		const entry = document.createElement('option');
		if (y === null) {
			entry.value = y;
			entry.innerText = '(No Year)';
		} else {
			entry.value = entry.innerText = y;
		};
		SearchEls.filters.year.appendChild(entry);
	});

	const issue_counts = new Set(results
        .map(r => r.issue_count)
        .sort((a, b) => a - b)
    );
	SearchEls.filters.issue_count.innerHTML = '';
	const all_issue_counts_option = document.createElement('option');
	all_issue_counts_option.value = '';
	all_issue_counts_option.innerText = 'All Issue Counts';
	all_issue_counts_option.selected = true;
	SearchEls.filters.issue_count.appendChild(all_issue_counts_option);
	const one_higher_issue_counts_option = document.createElement('option');
	one_higher_issue_counts_option.value = '>1 issues';
	one_higher_issue_counts_option.innerText = '>1 issues';
	SearchEls.filters.issue_count.appendChild(one_higher_issue_counts_option);

	issue_counts.forEach(ic => {
		const entry = document.createElement('option');
		entry.value = entry.innerText = `${ic} issues`;
		SearchEls.filters.issue_count.appendChild(entry);
	});

	const volume_numbers = new Set(results
        .map(r => r.volume_number)
        .sort((a, b) => a - b)
    );
	SearchEls.filters.volume_number.innerHTML = '';
	const all_volume_numbers_option = document.createElement('option');
	all_volume_numbers_option.value = '';
	all_volume_numbers_option.innerText = 'All Volume Numbers';
	all_volume_numbers_option.selected = true;
	SearchEls.filters.volume_number.appendChild(all_volume_numbers_option);

	volume_numbers.forEach(vn => {
		const entry = document.createElement('option');
		entry.value = entry.innerText = `Volume ${vn}`;
		SearchEls.filters.volume_number.appendChild(entry);
	});

	const publishers = new Set(results.map(r => r.publisher).sort());
	SearchEls.filters.publisher.innerHTML = '';
	const all_publishers_option = document.createElement('option');
	all_publishers_option.value = '';
	all_publishers_option.innerText = 'All Publishers';
	all_publishers_option.selected = true;
	SearchEls.filters.publisher.appendChild(all_publishers_option);

	publishers.forEach(pub => {
		const entry = document.createElement('option');
		entry.value = entry.innerText = pub;
		SearchEls.filters.publisher.appendChild(entry);
	});

    processURLFilters();
	applyFilters();
};

function search(reset_url_params=true) {
	const requestId = ++searchRequestId;
	const query = SearchEls.search_bar.input.value.trim();
	SearchEls.search_bar.input.blur();
	SearchEls.msgs.empty.querySelector('p').textContent =
		'No matching ComicVine series. Publisher issue results are shown separately below.';

	hide([
		SearchEls.msgs.empty,
		SearchEls.msgs.explain,
		SearchEls.msgs.failed,
		SearchEls.search_results
	], [
		SearchEls.msgs.loading
	]);

    if (reset_url_params) {
        emptyParams();
		history.replaceState({}, '', `?q=${encodeURIComponent(query)}`);
	}

    usingApiKey().then(api_key => {
		searchPublisherIssues(query, api_key, requestId);
		if (!SearchEls.msgs.blocked.classList.contains('hidden')) {
			hide([SearchEls.msgs.loading]);
			return;
		}
		fetchAPI('/volumes/search', api_key, {query: encodeURIComponent(query)})
		.then(json => {
			if (requestId !== searchRequestId) return;
			buildResults(json.result);

			if (!SearchEls.search_results.querySelector('button:not(.filter-bar)'))
				hide([SearchEls.msgs.loading], [SearchEls.msgs.empty]);
			else
				hide([SearchEls.msgs.loading], [SearchEls.search_results]);
		})
		.catch(e => {
			if (requestId !== searchRequestId) return;
			if (e.status === 400)
				hide([SearchEls.msgs.loading], [SearchEls.msgs.failed]);
			else {
				console.error('Series search failed:', e);
				hide([SearchEls.msgs.loading], [SearchEls.msgs.empty]);
				SearchEls.msgs.empty.querySelector('p').textContent =
					'Series search failed. Publisher issue results are independent of this search.';
			}
		});
	});
};

function clearSearch(e) {
	searchRequestId++;
	document.getElementById('publisher-issue-results').classList.add('hidden');
	hide([
		SearchEls.search_results,
		SearchEls.msgs.empty,
		SearchEls.msgs.failed
	], [
		SearchEls.msgs.explain
	]);
	SearchEls.search_results
        .querySelectorAll('button:not(.filter-bar)')
        .forEach(e => e.remove());
	SearchEls.search_bar.input.value = '';
    setURLParams();
};

function applyFilters() {
	const translation = SearchEls.filters.translations.value,
		year = SearchEls.filters.year.value,
		issue_count = (
			SearchEls.filters.issue_count.value || ' issues'
		).split(' issues')[0],
		volume_number = (
			SearchEls.filters.volume_number.value || 'Volume '
			).split('Volume ')[1],
		publisher = SearchEls.filters.publisher.value;

	setLocalStorage({'translated_filter': translation});

	let filter = '';

	if (translation === 'only-english')
		filter += '[data-_translated="false"]';
	if (year !== '')
		filter += `[data-_year="${year}"]`;
	if (issue_count === '>1')
		filter += `:not([data-_issue_count="0"]):not([data-_issue_count="1"])`
	else if (issue_count !== '')
		filter += `[data-_issue_count="${issue_count}"]`;
	if (volume_number !== '')
		filter += `[data-_volume_number="${volume_number}"]`;
	if (publisher !== '')
		filter += `[data-_publisher="${publisher}"]`;

	if (filter === '')
		hide([], SearchEls.search_results.querySelectorAll('button'));
	else
		hide(
			SearchEls.search_results.querySelectorAll('button'),
			SearchEls.search_results.querySelectorAll(`button${filter}`)
		);

    setURLParams();
};

//
// URL params
//
function emptyParams() {
    history.pushState(
        {}, "",
        window.location.origin + window.location.pathname
    );
};

function setURLParams() {
    const params = new URLSearchParams();

    if (SearchEls.search_bar.input.value === "") {
        emptyParams();
        return;
    };

    if (SearchEls.search_bar.input.value)
        params.set('q', SearchEls.search_bar.input.value);
    if (SearchEls.filters.translations.value)
        params.set('t', SearchEls.filters.translations.value);
    if (SearchEls.filters.year.value)
        params.set('y', SearchEls.filters.year.value);
    if (SearchEls.filters.issue_count.value)
        params.set('i', SearchEls.filters.issue_count.value);
    if (SearchEls.filters.volume_number.value)
        params.set('v', SearchEls.filters.volume_number.value);
    if (SearchEls.filters.publisher.value)
        params.set('p', SearchEls.filters.publisher.value);

    history.pushState({}, "", `?${params.toString()}`);
};

function processURLParams() {
    const params = new URLSearchParams(window.location.search);

    const query = params.get('q');
    if ((query || null) === null)
        return;
    SearchEls.search_bar.input.value = query;

    search(reset_url_params=false);
};

function processURLFilters() {
    const params = new URLSearchParams(window.location.search);

    if (params.get('t'))
        SearchEls.filters.translations.value = params.get('t');
    if (params.get('y'))
        SearchEls.filters.year.value = params.get('y');
    if (params.get('i'))
        SearchEls.filters.issue_count.value = params.get('i');
    if (params.get('v'))
        SearchEls.filters.volume_number.value = params.get('v');
    if (params.get('p'))
        SearchEls.filters.publisher.value = params.get('p');
};

// code run on load
usingApiKey().then(key => fetchAPI('/rootfolder', key))
	.then(json => {
		if (!json.result.length) hide([], [SearchEls.msgs.blocked]);
	})
	.catch(error => console.error('Could not check root folders:', error));

SearchEls.search_bar.cancel.onclick = clearSearch;
SearchEls.search_bar.bar.action = 'javascript:search();';

SearchEls.filters.translations.onchange =
SearchEls.filters.publisher.onchange =
SearchEls.filters.volume_number.onchange =
SearchEls.filters.year.onchange =
SearchEls.filters.issue_count.onchange =
	e => applyFilters();

const translated_filter = getLocalStorage('translated_filter')['translated_filter'];
SearchEls.filters.translations
    .querySelector(`option[value="${translated_filter}"]`)
    .setAttribute('selected', '');

processURLParams();
