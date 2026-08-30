const WeeklyEls = {
	query: document.querySelector('#weekly-query'),
	range: document.querySelector('#weekly-range'),
	publisher: document.querySelector('#weekly-publisher'),
	status: document.querySelector('#weekly-status'),
	refresh: document.querySelector('#weekly-refresh'),
	selectMissing: document.querySelector('#select-missing'),
	queueSelected: document.querySelector('#queue-selected'),
	count: document.querySelector('#weekly-count'),
	message: document.querySelector('#weekly-message'),
	loading: document.querySelector('#weekly-loading'),
	empty: document.querySelector('#weekly-empty'),
	list: document.querySelector('#weekly-list'),
	pagination: document.querySelector('#weekly-pagination'),
	previous: document.querySelector('#weekly-previous'),
	next: document.querySelector('#weekly-next'),
	page: document.querySelector('#weekly-page')
};

const WeeklyState = {
	packs: [],
	selected: new Set(),
	page: 1,
	totalPages: 1,
	request: 0
};

const statusLabels = {
	downloaded: 'Downloaded',
	missing_monitored: 'Missing Monitored',
	missing_unmonitored: 'Missing Unmonitored',
	metadata_pending: 'Metadata Pending',
	not_in_library: 'Not In Library',
	ambiguous: 'Needs Review'
};

function weeklyElement(tag, className='', text='') {
	const element = document.createElement(tag);
	if (className) element.className = className;
	if (text) element.innerText = text;
	return element;
}

function weeklyDate(value) {
	if (!value) return 'Unknown week';
	return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, {
		month: 'long', day: 'numeric', year: 'numeric'
	});
}

function setWeeklyState(visible) {
	[WeeklyEls.loading, WeeklyEls.empty, WeeklyEls.list].forEach(
		element => element.classList.add('hidden')
	);
	visible.classList.remove('hidden');
}

function createArchiveSection(pack) {
	if (!pack.archives.length) return null;
	const section = weeklyElement('div', 'weekly-archives');
	const heading = weeklyElement(
		'h3',
		'weekly-archive-heading',
		'External aggregate archives'
	);
	section.appendChild(heading);
	const warning = weeklyElement(
		'p',
		'weekly-archive-warning',
		'Large publisher bundles open externally and are never queued by Kapowarr.'
	);
	section.appendChild(warning);

	pack.archives.forEach(archive => {
		const row = weeklyElement('div', 'weekly-archive-row');
		const description = [
			archive.publisher || archive.title,
			archive.format,
			archive.size
		].filter(Boolean).join(' · ');
		row.appendChild(weeklyElement('span', '', description));
		const links = weeklyElement('span', 'weekly-archive-links');
		archive.links.forEach(link => {
			const anchor = weeklyElement('a', '', link.label || 'Open');
			anchor.href = link.url;
			anchor.target = '_blank';
			anchor.rel = 'noopener noreferrer';
			links.appendChild(anchor);
		});
		row.appendChild(links);
		section.appendChild(row);
	});
	return section;
}

function createIssueRow(item) {
	const row = weeklyElement('div', 'weekly-item');
	row.dataset.recordKey = item.record_key;
	row.dataset.status = item.local_status;

	const selection = weeklyElement('label', 'weekly-select');
	const checkbox = document.createElement('input');
	checkbox.type = 'checkbox';
	checkbox.disabled = item.local_status !== 'missing_monitored';
	checkbox.checked = WeeklyState.selected.has(item.record_key);
	checkbox.ariaLabel = `Select ${item.display_title}`;
	checkbox.onchange = () => {
		if (checkbox.checked)
			WeeklyState.selected.add(item.record_key);
		else
			WeeklyState.selected.delete(item.record_key);
		updateSelectedCount();
	};
	selection.appendChild(checkbox);
	row.appendChild(selection);

	const issue = weeklyElement('div', 'weekly-issue');
	const title = weeklyElement('a', 'weekly-title', item.display_title);
	title.href = item.external_url;
	title.target = '_blank';
	title.rel = 'noopener noreferrer';
	issue.appendChild(title);
	issue.appendChild(weeklyElement(
		'span',
		'weekly-match-reason',
		item.match_reason
	));
	row.appendChild(issue);

	row.appendChild(weeklyElement(
		'span',
		'weekly-publisher',
		item.publisher || 'Unknown'
	));
	row.appendChild(weeklyElement(
		'span',
		`weekly-status status-${item.local_status}`,
		statusLabels[item.local_status] || item.local_status
	));
	return row;
}

function createWeek(pack, index) {
	const details = weeklyElement('details', 'weekly-pack');
	details.open = index === 0;
	const summary = weeklyElement('summary', 'weekly-pack-summary');
	const summaryTitle = weeklyElement('span', 'weekly-pack-title');
	summaryTitle.appendChild(weeklyElement('strong', '', weeklyDate(pack.week_date)));
	summaryTitle.appendChild(weeklyElement(
		'span',
		'',
		`${pack.items.length} individual issues`
	));
	summary.appendChild(summaryTitle);
	const external = weeklyElement('a', 'weekly-pack-link', 'Open GetComics');
	external.href = pack.external_url;
	external.target = '_blank';
	external.rel = 'noopener noreferrer';
	external.onclick = event => event.stopPropagation();
	summary.appendChild(external);
	details.appendChild(summary);

	const archiveSection = createArchiveSection(pack);
	if (archiveSection) details.appendChild(archiveSection);
	const issues = weeklyElement('div', 'weekly-items');
	pack.items.forEach(item => issues.appendChild(createIssueRow(item)));
	details.appendChild(issues);
	return details;
}

function updateSelectedCount() {
	const count = WeeklyState.selected.size;
	WeeklyEls.queueSelected.disabled = count === 0;
	WeeklyEls.queueSelected.querySelector('p').innerText = count
		? `Queue Selected (${count})`
		: 'Queue Selected';
}

function renderWeeklyPacks(result) {
	WeeklyState.packs = result.packs;
	WeeklyState.totalPages = result.total_pages;
	WeeklyEls.list.innerHTML = '';
	result.packs.forEach((pack, index) => {
		WeeklyEls.list.appendChild(createWeek(pack, index));
	});
	WeeklyEls.count.innerText =
		`${result.total_items} issues across ${result.total_packs} weeks`;

	if (result.packs.length)
		setWeeklyState(WeeklyEls.list);
	else
		setWeeklyState(WeeklyEls.empty);

	WeeklyEls.pagination.classList.toggle('hidden', result.total_pages <= 1);
	WeeklyEls.page.innerText = `Page ${result.page} of ${result.total_pages}`;
	WeeklyEls.previous.disabled = result.page <= 1;
	WeeklyEls.next.disabled = result.page >= result.total_pages;
	updateSelectedCount();
}

function fetchWeeklyPacks(apiKey, forceRefresh=false) {
	const requestId = ++WeeklyState.request;
	setWeeklyState(WeeklyEls.loading);
	WeeklyEls.message.innerText = '';
	const params = {
		weeks: WeeklyEls.range.value,
		page: WeeklyState.page,
		per_page: 20
	};
	if (WeeklyEls.publisher.value)
		params.publisher = WeeklyEls.publisher.value;
	if (WeeklyEls.status.value)
		params.local_status = WeeklyEls.status.value;
	if (WeeklyEls.query.value.trim())
		params.query = encodeURIComponent(WeeklyEls.query.value.trim());
	if (forceRefresh)
		params.force_refresh = 'true';

	return fetchAPI('/weekly-packs', apiKey, params)
		.then(json => {
			if (requestId === WeeklyState.request)
				renderWeeklyPacks(json.result);
		})
		.catch(() => {
			if (requestId !== WeeklyState.request) return;
			setWeeklyState(WeeklyEls.empty);
			WeeklyEls.message.innerText = 'Weekly Packs could not be loaded.';
		});
}

function queueSelected(apiKey) {
	if (!WeeklyState.selected.size) return;
	WeeklyEls.queueSelected.disabled = true;
	WeeklyEls.message.innerText = 'Validating selected issues...';
	sendAPI('POST', '/weekly-packs', apiKey, {}, {
		record_keys: [...WeeklyState.selected],
		weeks: parseInt(WeeklyEls.range.value)
	})
		.then(response => response.json())
		.then(json => {
			const summary = Object.entries(json.result.counts)
				.map(([status, count]) => `${status.replaceAll('_', ' ')}: ${count}`)
				.join(' · ');
			json.result.items.forEach(item => {
				if (item.status === 'queued' || item.status === 'already_queued')
					WeeklyState.selected.delete(item.record_key);
			});
			return fetchWeeklyPacks(apiKey).then(() => {
				WeeklyEls.message.innerText = summary;
			});
		})
		.catch(() => {
			WeeklyEls.message.innerText = 'Selected issues could not be queued.';
			updateSelectedCount();
		});
}

let queryTimer = null;
usingApiKey().then(apiKey => {
	if (!apiKey) return;
	fetchWeeklyPacks(apiKey);

	WeeklyEls.refresh.onclick = () => fetchWeeklyPacks(apiKey, true);
	WeeklyEls.selectMissing.onclick = () => {
		document.querySelectorAll(
			'.weekly-item[data-status="missing_monitored"] input'
		).forEach(checkbox => {
			checkbox.checked = true;
			WeeklyState.selected.add(
				checkbox.closest('.weekly-item').dataset.recordKey
			);
		});
		updateSelectedCount();
	};
	WeeklyEls.queueSelected.onclick = () => queueSelected(apiKey);

	[WeeklyEls.range, WeeklyEls.publisher, WeeklyEls.status].forEach(
		control => control.onchange = () => {
			WeeklyState.page = 1;
			WeeklyState.selected.clear();
			fetchWeeklyPacks(apiKey);
		}
	);
	WeeklyEls.query.oninput = () => {
		clearTimeout(queryTimer);
		queryTimer = setTimeout(() => {
			WeeklyState.page = 1;
			WeeklyState.selected.clear();
			fetchWeeklyPacks(apiKey);
		}, 250);
	};
	WeeklyEls.previous.onclick = () => {
		WeeklyState.page -= 1;
		fetchWeeklyPacks(apiKey);
	};
	WeeklyEls.next.onclick = () => {
		WeeklyState.page += 1;
		fetchWeeklyPacks(apiKey);
	};
});