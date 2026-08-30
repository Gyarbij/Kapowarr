const WeeklyEls = {
	query: document.querySelector('#weekly-query'),
	range: document.querySelector('#weekly-range'),
	publisher: document.querySelector('#weekly-publisher'),
	status: document.querySelector('#weekly-status'),
	refresh: document.querySelector('#weekly-refresh'),
	expand: document.querySelector('#weekly-expand'),
	collapse: document.querySelector('#weekly-collapse'),
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
	openWeeks: new Set(),
	openStateInitialized: false,
	apiKey: null,
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
	row.appendChild(createItemActions(item));
	return row;
}

function createActionLink(href, label, icon) {
	const link = weeklyElement('a', 'weekly-action icon-text-color');
	link.href = href;
	link.title = label;
	link.ariaLabel = label;
	const image = document.createElement('img');
	image.src = `${url_base}/static/img/${icon}`;
	image.alt = '';
	link.appendChild(image);
	return link;
}

function createQueueAction(item, action, label) {
	const button = weeklyElement('button', 'weekly-action icon-text-color');
	button.type = 'button';
	button.title = label;
	button.ariaLabel = label;
	const image = document.createElement('img');
	image.src = `${url_base}/static/img/download.svg`;
	image.alt = '';
	button.appendChild(image);
	button.onclick = () => queueWeeklyItems(
		WeeklyState.apiKey,
		[item.record_key],
		action,
		button
	);
	return button;
}

function addVolumeSearchURL(item) {
	const params = new URLSearchParams({q: item.series_title || item.display_title});
	if (item.issue_year)
		params.set('y', item.issue_year);
	return `${url_base}/add?${params.toString()}`;
}

function createItemActions(item) {
	const actions = weeklyElement('div', 'weekly-actions');
	if (item.local_volume_id) {
		actions.appendChild(createActionLink(
			`${url_base}/volumes/${item.local_volume_id}`,
			`Open ${item.series_title} in library`,
			'files.svg'
		));
	}

	if (item.local_status === 'missing_monitored') {
		actions.appendChild(createQueueAction(
			item,
			'download',
			`Download ${item.display_title}`
		));
	} else if (item.local_status === 'missing_unmonitored') {
		actions.appendChild(createQueueAction(
			item,
			'monitor_and_download',
			`Monitor and download ${item.display_title}`
		));
	} else if (
		item.local_status === 'not_in_library'
		|| (item.local_status === 'ambiguous' && !item.local_volume_id)
	) {
		actions.appendChild(createActionLink(
			addVolumeSearchURL(item),
			`Find ${item.series_title} in canonical metadata`,
			'search.svg'
		));
	}
	return actions;
}

function weeklyPackKey(pack) {
	return pack.week_date || String(pack.id);
}

function createWeek(pack) {
	const details = weeklyElement('details', 'weekly-pack');
	const packKey = weeklyPackKey(pack);
	details.dataset.week = packKey;
	details.open = WeeklyState.openWeeks.has(packKey);
	details.addEventListener('toggle', () => {
		if (details.open)
			WeeklyState.openWeeks.add(packKey);
		else
			WeeklyState.openWeeks.delete(packKey);
	});
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
	if (!WeeklyState.openStateInitialized) {
		if (result.packs.length)
			WeeklyState.openWeeks.add(weeklyPackKey(result.packs[0]));
		WeeklyState.openStateInitialized = true;
	}
	WeeklyEls.list.innerHTML = '';
	result.packs.forEach(pack => {
		WeeklyEls.list.appendChild(createWeek(pack));
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

function setAllWeeksOpen(open) {
	document.querySelectorAll('.weekly-pack').forEach(details => {
		details.open = open;
		if (open)
			WeeklyState.openWeeks.add(details.dataset.week);
		else
			WeeklyState.openWeeks.delete(details.dataset.week);
	});
}

function restoreWeeklyOption(select, value) {
	if ([...select.options].some(option => option.value === value))
		select.value = value;
}

function applyWeeklyURLParams() {
	const params = new URLSearchParams(window.location.search);
	restoreWeeklyOption(WeeklyEls.range, params.get('weeks'));
	restoreWeeklyOption(WeeklyEls.publisher, params.get('publisher'));
	restoreWeeklyOption(WeeklyEls.status, params.get('local_status'));
	WeeklyEls.query.value = params.get('query') || '';
	const page = parseInt(params.get('page'));
	WeeklyState.page = Number.isInteger(page) && page > 0 ? page : 1;
}

function updateWeeklyURL() {
	const params = new URLSearchParams();
	if (WeeklyEls.range.value !== '8')
		params.set('weeks', WeeklyEls.range.value);
	if (WeeklyEls.publisher.value)
		params.set('publisher', WeeklyEls.publisher.value);
	if (WeeklyEls.status.value)
		params.set('local_status', WeeklyEls.status.value);
	if (WeeklyEls.query.value.trim())
		params.set('query', WeeklyEls.query.value.trim());
	if (WeeklyState.page > 1)
		params.set('page', WeeklyState.page);
	const query = params.toString();
	history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
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

function queueWeeklyItems(apiKey, recordKeys, action='download', trigger=null) {
	if (!recordKeys.length) return Promise.resolve();
	if (trigger) trigger.disabled = true;
	else WeeklyEls.queueSelected.disabled = true;
	WeeklyEls.message.innerText = action === 'monitor_and_download'
		? 'Monitoring and validating issue...'
		: 'Validating selected issues...';
	return sendAPI('POST', '/weekly-packs', apiKey, {}, {
		record_keys: recordKeys,
		weeks: parseInt(WeeklyEls.range.value),
		action
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
			WeeklyEls.message.innerText = action === 'monitor_and_download'
				? 'Issue could not be monitored and queued.'
				: 'Selected issues could not be queued.';
			updateSelectedCount();
		})
		.finally(() => {
			if (trigger) trigger.disabled = false;
		});
}

let queryTimer = null;
usingApiKey().then(apiKey => {
	if (!apiKey) return;
	WeeklyState.apiKey = apiKey;
	applyWeeklyURLParams();
	fetchWeeklyPacks(apiKey);

	WeeklyEls.refresh.onclick = () => fetchWeeklyPacks(apiKey, true);
	WeeklyEls.expand.onclick = () => setAllWeeksOpen(true);
	WeeklyEls.collapse.onclick = () => setAllWeeksOpen(false);
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
	WeeklyEls.queueSelected.onclick = () => queueWeeklyItems(
		apiKey,
		[...WeeklyState.selected]
	);

	[WeeklyEls.range, WeeklyEls.publisher, WeeklyEls.status].forEach(
		control => control.onchange = () => {
			WeeklyState.page = 1;
			WeeklyState.selected.clear();
			updateWeeklyURL();
			fetchWeeklyPacks(apiKey);
		}
	);
	WeeklyEls.query.oninput = () => {
		clearTimeout(queryTimer);
		queryTimer = setTimeout(() => {
			WeeklyState.page = 1;
			WeeklyState.selected.clear();
			updateWeeklyURL();
			fetchWeeklyPacks(apiKey);
		}, 250);
	};
	WeeklyEls.previous.onclick = () => {
		WeeklyState.page -= 1;
		updateWeeklyURL();
		fetchWeeklyPacks(apiKey);
	};
	WeeklyEls.next.onclick = () => {
		WeeklyState.page += 1;
		updateWeeklyURL();
		fetchWeeklyPacks(apiKey);
	};
});