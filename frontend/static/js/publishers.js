// Publishers page JavaScript

const publisherList = document.getElementById('publisher-list');
const issuesGrid = document.getElementById('issues-grid');
const volumesGrid = document.getElementById('volumes-grid');
const loadingIndicator = document.getElementById('loading');
const emptyState = document.getElementById('empty-state');
const refreshButton = document.getElementById('refresh-button');
const backButton = document.getElementById('back-button');
const searchInput = document.getElementById('search-input');
const searchForm = document.getElementById('search-container');
const clearSearchBtn = document.getElementById('clear-search');
const currentViewLabel = document.getElementById('current-view-label');
const publisherSortSelect = document.getElementById('publisher-sort');
const publisherCount = document.getElementById('publisher-count');
const loadMoreButton = document.getElementById('load-more-button');
const viewSelect = document.getElementById('publisher-view');
const periodSelect = document.getElementById('publisher-period');
const statusSelect = document.getElementById('publisher-status');
const summary = document.getElementById('publisher-summary');
const notice = document.getElementById('publisher-notice');

let rootFoldersLoaded = false;

const addWindowEls = {
	form: document.querySelector('#add-form'),
	title: document.querySelector('#add-window h2'),
	cover: document.querySelector('#add-cover'),
	cv_input: document.querySelector('#comicvine-input'),
	monitor_volume_input: document.querySelector('#monitor-volume-input'),
	monitor_issues_input: document.querySelector('#monitor-issues-input'),
	monitoring_scheme: document.querySelector('#monitoring-scheme-input'),
	root_folder_input: document.querySelector('#rootfolder-input'),
	volume_folder_input: document.querySelector('#volumefolder-input'),
	special_state_input: document.querySelector('#specialoverride-input'),
	auto_search_input: document.querySelector('#auto-search-input'),
	submit: document.querySelector('#add-volume')
};

// Major publishers (ComicVine IDs)
const MAJOR_PUBLISHERS = {
	31: 'Marvel',
	10: 'DC Comics',
	92: 'Image',
	106: 'Dark Horse',
	95: 'IDW Publishing',
	750: 'Boom! Studios',
	364: 'Dynamite',
	311: 'Vertigo',
	623: 'Valiant'
};

let allPublishers = [];
let publisherFacets = [];
let currentIssues = [];
let currentVolumes = [];
let currentPublisher = null;
let searchQuery = '';
let visibleVolumeCount = 48;
let catalogueRequestId = 0;
let issuePage = 1;
let publisherCatalogueLoaded = false;
let searchTimer;

// Create a publisher card
function createPublisherCard(publisher) {
	const card = document.createElement('button');
	card.type = 'button';
	card.className = 'publisher-card';
	card.dataset.publisher = publisher.key;
	const selected = (currentPublisher?.key || '') === publisher.key;
	card.setAttribute('aria-pressed', String(selected));
	card.appendChild(IssueCatalog.element('span', 'publisher-name', publisher.name));
	if (publisher.issue_count != null)
		card.appendChild(IssueCatalog.element('span', 'publisher-issue-count', String(publisher.issue_count)));
	card.onclick = () => {
		currentPublisher = publisher.key ? publisher : null;
		loadCurrentView();
	};
	return card;
}

// Create a volume card
function createVolumeCard(volume) {
	const card = document.createElement('article');
	card.className = 'volume-card';
	card.dataset.cvId = volume.comicvine_id;
	if (volume.already_added) {
		card.classList.add('in-library');
	}
	const link = document.createElement('a');
	const query = `${volume.title}${volume.year ? ` (${volume.year})` : ''}`;
	link.href = volume.already_added
		? `${url_base}/volumes/${volume.already_added}`
		: `${url_base}/add?q=${encodeURIComponent(query)}`;
	const cover = IssueCatalog.element('img', 'volume-cover');
	cover.src = IssueCatalog.externalURL(volume.cover_link) || `${url_base}/static/img/placeholder.svg`;
	cover.alt = '';
	cover.loading = 'lazy';
	link.appendChild(cover);
	const info = IssueCatalog.element('div', 'volume-info');
	info.appendChild(IssueCatalog.element('h3', 'volume-title', volume.title));
	info.appendChild(IssueCatalog.element('p', 'volume-year', String(volume.year || 'Unknown year')));
	info.appendChild(IssueCatalog.element('p', 'volume-year', `${volume.issue_count} issues`));
	link.appendChild(info);
	card.appendChild(link);
	if (volume.already_added)
		card.appendChild(IssueCatalog.element('span', 'volume-badge', 'In library'));
	if (!volume.already_added) {
		const actions = IssueCatalog.element('div', 'volume-actions');
			const actionBtn = IssueCatalog.element('button', 'catalog-action', 'Add series');
		actionBtn.type = 'button';
		actionBtn.addEventListener('click', event => {
			showAddWindow(volume, actionBtn);
		});
		actions.appendChild(actionBtn);
		card.appendChild(actions);
	}

	return card;
}

function ensureRootFolders(api_key) {
	if (rootFoldersLoaded) return Promise.resolve();
	return fetchAPI('/rootfolder', api_key)
	.then(json => {
		addWindowEls.root_folder_input.innerHTML = '';
		if (json.result.length) {
			json.result.forEach(folder => {
				const option = document.createElement('option');
				option.value = folder.id;
				option.innerText = folder.folder;
				addWindowEls.root_folder_input.appendChild(option);
			});
			rootFoldersLoaded = true;
		}
	});
}

function fillAddWindow(volumeData, folderName) {
	addWindowEls.title.innerText = volumeData.title || 'Add volume';
	addWindowEls.cover.src = volumeData.cover_link || `${url_base}/static/img/placeholder.svg`;
	addWindowEls.cv_input.value = volumeData.comicvine_id;
	addWindowEls.volume_folder_input.value = folderName || '';
	addWindowEls.form.dataset.volume_folder = folderName || '';
	addWindowEls.submit.innerText = 'Add Volume';
	addWindowEls.special_state_input.value = 'auto';

	const monitoringPref = getLocalStorage(
		'monitor_new_volume', 'monitor_new_issues', 'monitoring_scheme'
	);
	addWindowEls.monitor_volume_input.value = monitoringPref.monitor_new_volume;
	addWindowEls.monitor_issues_input.value = monitoringPref.monitor_new_issues;
	addWindowEls.monitoring_scheme.value = monitoringPref.monitoring_scheme;
}

function showAddWindow(volume, actionButton) {
	if (!addWindowEls.form) return;
	if (actionButton) actionButton.disabled = true;

	usingApiKey()
	.then(api_key => Promise.all([
		ensureRootFolders(api_key),
		fetchAPI('/volumes/metadata', api_key, { comicvine_id: volume.comicvine_id })
			.then(volumeResponse => ({ api_key, volumeResponse }))
	]))
	.then(([, { api_key, volumeResponse }]) => {
		if (!volumeResponse.result) {
			throw new Error(volumeResponse.error || 'Failed to fetch volume');
		}
		const volumeData = volumeResponse.result;
		const folderBody = {
			comicvine_id: volumeData.comicvine_id,
			title: volumeData.title,
			year: volumeData.year || null,
			volume_number: volumeData.volume_number,
			publisher: volumeData.publisher || null
		};
		return Promise.all([
			Promise.resolve(volumeData),
			sendAPI('POST', '/volumes/search', api_key, {}, folderBody)
				.then(response => response.json())
		]);
	})
	.then(([volumeData, folderResponse]) => {
		if (folderResponse.result?.folder) {
			volumeData._volume_folder = folderResponse.result.folder;
		}
		fillAddWindow(volumeData, volumeData._volume_folder || '');
		showWindow('add-window');
	})
	.catch(error => {
		console.error('Error preparing add window:', error);
		summary.textContent = 'Could not load the series details. Check your metadata API settings and try again.';
	})
	.finally(() => {
		if (actionButton) actionButton.disabled = false;
	});
}

function addVolume() {
	showLoadWindow('add-window');
	const volumeFolder = addWindowEls.volume_folder_input.value;

	const data = {
		comicvine_id: parseInt(addWindowEls.cv_input.value),
		root_folder_id: parseInt(addWindowEls.root_folder_input.value),
		monitor: addWindowEls.monitor_volume_input.value === 'true',
		monitoring_scheme: addWindowEls.monitoring_scheme.value,
		monitor_new_issues: addWindowEls.monitor_issues_input.value === 'true',
		volume_folder: '',
		special_version: addWindowEls.special_state_input.value || null,
		auto_search: addWindowEls.auto_search_input.checked
	};

	if (volumeFolder !== '' && addWindowEls.form.dataset.volume_folder) {
		if (volumeFolder !== addWindowEls.form.dataset.volume_folder) {
			data.volume_folder = volumeFolder;
		}
	}

	setLocalStorage({
		monitor_new_volume: data.monitor,
		monitor_new_issues: data.monitor_new_issues,
		monitoring_scheme: data.monitoring_scheme
	});

	usingApiKey()
	.then(api_key => sendAPI('POST', '/volumes', api_key, {}, data))
	.then(response => response.json())
	.then(json => {
		const addedVolumeId = json.result?.id;
		if (addedVolumeId) loadCurrentView();
		closeWindow();
	})
	.catch(e => {
		if (e.status === 509) {
			addWindowEls.submit.innerText = 'ComicVine API rate limit reached';
			showWindow('add-window');
		} else if (e.status === 400) {
			addWindowEls.submit.innerText = 'Volume folder is parent or child of other volume folder';
			showWindow('add-window');
		} else {
			console.error(e);
		}
	});
}

function publishersInScope() {
	const publishers = new Map(publisherFacets.map(publisher => [publisher.key, {...publisher}]));
	allPublishers.forEach(publisher => {
		const key = IssueCatalog.publisherKey(publisher.name);
		publishers.set(key, {...publishers.get(key), ...publisher, key});
	});
	if (currentPublisher && !publishers.has(currentPublisher.key))
		publishers.set(currentPublisher.key, currentPublisher);
	return [...publishers.values()].sort((a, b) => {
		const major = Object.values(MAJOR_PUBLISHERS).map(name => IssueCatalog.publisherKey(name));
		return Number(major.includes(b.key)) - Number(major.includes(a.key))
			|| a.name.localeCompare(b.name);
	});
}

function renderPublisherList() {
	const publishers = publishersInScope();
	if (currentPublisher)
		currentPublisher = publishers.find(publisher => publisher.key === currentPublisher.key) || currentPublisher;
	const total = publisherFacets.reduce((count, publisher) => count + publisher.issue_count, 0);
	publisherList.replaceChildren(
		createPublisherCard({key: '', name: 'All publishers', issue_count: total}),
		...publishers.map(createPublisherCard)
	);
}

function setBusy(busy) {
	loadingIndicator.classList.toggle('hidden', !busy);
	refreshButton.disabled = busy;
	loadMoreButton.disabled = busy;
}

function showEmpty(message) {
	emptyState.querySelector('p').textContent = message;
	emptyState.classList.remove('hidden');
}

function rememberView() {
	const params = new URLSearchParams({
		view: viewSelect.value,
		period: periodSelect.value,
		sort: publisherSortSelect.value
	});
	if (currentPublisher) {
		params.set('publisher', currentPublisher.key);
		params.set('name', currentPublisher.name);
	}
	if (searchQuery) params.set('query', searchQuery);
	if (statusSelect.value) params.set('status', statusSelect.value);
	history.replaceState(null, '', `${url_base}/publishers?${params}`);
	setLocalStorage({
		publisher_view: viewSelect.value,
		publisher_period: periodSelect.value,
		publisher_sort: publisherSortSelect.value,
		publisher_search: searchQuery,
		publisher_selected: currentPublisher?.key || '',
		publisher_selected_name: currentPublisher?.name || '',
		publisher_status: statusSelect.value
	});
}

async function fetchIssues(forceRefresh=false, append=false) {
	const requestId = ++catalogueRequestId;
	const nextPage = append ? issuePage + 1 : 1;
	setBusy(true);
	if (!append) {
		emptyState.classList.add('hidden');
		issuesGrid.replaceChildren();
		loadMoreButton.classList.add('hidden');
	}
	try {
		const params = {
			...IssueCatalog.dateRange(periodSelect.value),
			query: encodeURIComponent(searchQuery),
			publisher: encodeURIComponent(currentPublisher?.key || ''),
			local_status: statusSelect.value,
			sort: publisherSortSelect.value,
			page: nextPage,
			per_page: 48
		};
		if (forceRefresh) params.force_refresh = 'true';
		const data = await fetchAPI('/releases/search', apiKey, params);
		if (requestId !== catalogueRequestId) return;
		const result = data.result;
		publisherFacets = result.publishers;
		renderPublisherList();
		currentViewLabel.textContent = currentPublisher?.name || 'All publishers';
		currentIssues = append ? [...currentIssues, ...result.items] : result.items;
		issuePage = result.page;
		if (append)
			issuesGrid.append(...result.items.map(issue => IssueCatalog.card(issue)));
		else
			issuesGrid.replaceChildren(...result.items.map(issue => IssueCatalog.card(issue)));
		publisherCount.textContent = `${currentIssues.length} of ${result.total} issues`;
		summary.textContent = `${result.start_date} to ${result.end_date}${searchQuery ? ` - matching "${searchQuery}"` : ''}`;
		emptyState.classList.toggle('hidden', result.total !== 0);
		if (!result.total)
			showEmpty('No matching issues in this release window. Try another range or clear the search; your selected publisher is kept.');
		loadMoreButton.classList.toggle('hidden', result.page >= result.total_pages);
	} catch (error) {
		if (requestId !== catalogueRequestId) return;
		console.error('Publisher issue catalogue failed:', error);
		summary.textContent = 'Could not load publisher issues.';
		showEmpty('Issue discovery could not be loaded. Try Refresh or check the application logs.');
	} finally {
		if (requestId === catalogueRequestId) setBusy(false);
	}
}

function renderVolumes() {
	const volumes = [...currentVolumes].sort((a, b) => a.title.localeCompare(b.title));
	volumesGrid.replaceChildren(...volumes.slice(0, visibleVolumeCount).map(createVolumeCard));
	publisherCount.textContent = `${Math.min(visibleVolumeCount, volumes.length)} of ${volumes.length} series`;
	loadMoreButton.classList.toggle('hidden', visibleVolumeCount >= volumes.length);
	if (!volumes.length)
		showEmpty('No matching series in the canonical catalogue. Switch to Issues to search publisher listings, including new issues not linked to a library series yet.');
}

async function fetchSeries(forceRefresh=false) {
	const requestId = ++catalogueRequestId;
	setBusy(true);
	emptyState.classList.add('hidden');
	volumesGrid.replaceChildren();
	loadMoreButton.classList.add('hidden');
	try {
		if (!publisherCatalogueLoaded || forceRefresh) {
			const params = {limit: 1000};
			if (forceRefresh) params.force_refresh = 'true';
			const publishers = await fetchAPI('/publishers', apiKey, params);
			if (requestId !== catalogueRequestId) return;
			allPublishers = publishers.result;
			publisherCatalogueLoaded = true;
			renderPublisherList();
		}
		let data;
		if (searchQuery) {
			data = await fetchAPI('/volumes/search', apiKey, {query: encodeURIComponent(searchQuery)});
		} else if (currentPublisher?.comicvine_id) {
			const params = {limit: 1000};
			if (forceRefresh) params.force_refresh = 'true';
			data = await fetchAPI(`/publishers/${currentPublisher.comicvine_id}/volumes`, apiKey, params);
		} else {
			showEmpty('Choose a publisher or search by series title. To search individual issues or publisher URLs, use Issues.');
			publisherCount.textContent = '';
			return;
		}
		if (requestId !== catalogueRequestId) return;
		currentVolumes = data.result.filter(volume =>
			!currentPublisher || IssueCatalog.publisherKey(volume.publisher) === currentPublisher.key
		);
		visibleVolumeCount = 48;
		renderVolumes();
		summary.textContent = searchQuery
			? 'Canonical series search results. Publisher issue listings are available in Issues.'
			: 'Recently updated series from the canonical publisher catalogue (up to 1,000). Search a title for a targeted lookup.';
	} catch (error) {
		if (requestId !== catalogueRequestId) return;
		console.error('Publisher series catalogue failed:', error);
		summary.textContent = 'The canonical series catalogue is unavailable.';
		showEmpty('Check your metadata API settings. You can still browse publisher issues without a ComicVine key by switching to Issues.');
	} finally {
		if (requestId === catalogueRequestId) setBusy(false);
	}
}

function loadCurrentView(forceRefresh=false) {
	clearTimeout(searchTimer);
	searchQuery = searchInput.value.trim();
	document.querySelector('.publisher-workspace').scrollTop = 0;
	const issues = viewSelect.value === 'issues';
	currentViewLabel.textContent = currentPublisher?.name || 'All publishers';
	backButton.classList.toggle('hidden', currentPublisher === null);
	issuesGrid.classList.toggle('hidden', !issues);
	volumesGrid.classList.toggle('hidden', issues);
	periodSelect.disabled = !issues;
	statusSelect.disabled = !issues;
	publisherSortSelect.disabled = !issues;
	searchInput.placeholder = issues ? 'Title, #issue or publisher URL...' : 'Search series titles...';
	notice.textContent = issues
		? 'Publisher issue listings are independent of ComicVine. Find a new issue here even when its library series is not available yet. "Find library series" uses the normal add-volume flow.'
		: 'Series are canonical library entries. Searching stays within your selected publisher; use Issues for individual issue numbers and publisher listings.';
	renderPublisherList();
	rememberView();
	if (issues) return fetchIssues(forceRefresh);
	return fetchSeries(forceRefresh);
}

function handleSearch(event) {
	if (event) event.preventDefault();
	searchQuery = searchInput.value.trim();
	loadCurrentView();
}

let apiKey = null;
usingApiKey().then(key => {
	if (!key) return;
	apiKey = key;
	addWindowEls.form.action = 'javascript:addVolume();';
	const preferences = getLocalStorage(
		'publisher_view', 'publisher_period', 'publisher_sort', 'publisher_search',
		'publisher_selected', 'publisher_selected_name', 'publisher_status'
	);
	const params = new URLSearchParams(location.search);
	const hasURLState = ['view', 'period', 'sort', 'publisher', 'query', 'status']
		.some(key => params.has(key));
	const setting = (key, fallback) => params.get(key) ?? (
		hasURLState ? fallback : preferences[`publisher_${key}`] ?? fallback
	);
	viewSelect.value = setting('view', 'issues');
	periodSelect.value = setting('period', 'recent');
	publisherSortSelect.value = setting('sort', 'date-desc');
	statusSelect.value = setting('status', '');
	if (!viewSelect.value) viewSelect.value = 'issues';
	if (!periodSelect.value) periodSelect.value = 'recent';
	if (!publisherSortSelect.value) publisherSortSelect.value = 'date-desc';
	searchQuery = params.get('query') ?? (hasURLState ? '' : preferences.publisher_search ?? '');
	searchInput.value = searchQuery;
	const selected = params.get('publisher') ?? (hasURLState ? '' : preferences.publisher_selected);
	if (selected) {
		currentPublisher = {
			key: IssueCatalog.publisherKey(selected),
			name: params.get('name') || (
				params.has('publisher') ? selected
				: preferences.publisher_selected_name || selected
			)
		};
	}
	refreshButton.onclick = () => loadCurrentView(true);
	backButton.onclick = () => {
		currentPublisher = null;
		loadCurrentView();
	};
	searchForm.onsubmit = handleSearch;
	searchInput.oninput = () => {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(handleSearch, 350);
	};
	clearSearchBtn.onclick = () => {
		searchInput.value = '';
		handleSearch();
	};
	[viewSelect, periodSelect, statusSelect, publisherSortSelect].forEach(
		control => { control.onchange = () => loadCurrentView(); }
	);
	loadMoreButton.onclick = () => {
		if (viewSelect.value === 'issues')
			fetchIssues(false, true);
		else {
			visibleVolumeCount += 48;
			renderVolumes();
		}
	};
	socketReady.then(socket => {
		if (!socket) return;
		socket.on('task_ended', task => {
			if (task.action === 'update_new_releases' && !task.cancelled
				&& viewSelect.value === 'issues')
				fetchIssues();
		});
	});
	loadCurrentView();
});
