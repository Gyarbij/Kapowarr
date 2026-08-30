// Publishers page JavaScript

const publishersGrid = document.getElementById('publishers-grid');
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
let currentVolumes = [];
let currentPublisher = null;
let searchQuery = '';
let visiblePublisherCount = 100;
let visibleVolumeCount = 100;
let publisherRequestId = 0;
let volumeRequestId = 0;

// Create a publisher card
function createPublisherCard(publisher) {
	const card = document.createElement('div');
	card.className = 'publisher-card';
	card.dataset.cvId = publisher.comicvine_id;

	// Mark major publishers
	if (MAJOR_PUBLISHERS[publisher.comicvine_id]) {
		card.classList.add('major');
	}

	const volumeCount = publisher.volume_count
		? `${publisher.volume_count} volume${publisher.volume_count === 1 ? '' : 's'}`
		: 'Browse volumes';
	card.innerHTML = `
		<h3 class="publisher-name" title="${publisher.name}">${publisher.name}</h3>
		<p class="publisher-volumes">${volumeCount}</p>
	`;

	card.addEventListener('click', () => {
		showPublisherVolumes(publisher);
	});

	return card;
}

// Create a volume card
function createVolumeCard(volume) {
	const card = document.createElement('a');
	card.className = 'volume-card';
	card.dataset.cvId = volume.comicvine_id;
	
	if (volume.already_added) {
		card.classList.add('in-library');
		card.href = `${url_base}/volumes/${volume.already_added}`;
	} else {
		card.href = `${url_base}/add?cv_id=${volume.comicvine_id}`;
	}

	const coverUrl = volume.cover_link || `${url_base}/static/img/placeholder.svg`;

	card.innerHTML = `
		<img class="volume-cover" src="${coverUrl}" alt="${volume.title}" loading="lazy">
		<div class="volume-info">
			<h3 class="volume-title" title="${volume.title}">${volume.title}</h3>
			<p class="volume-year">${volume.year || 'Unknown year'}</p>
			<span class="volume-badge in-library">In Library</span>
		</div>
	`;

	if (!volume.already_added) {
		const actions = document.createElement('div');
		actions.className = 'volume-actions';
		actions.innerHTML = `
			<button class="volume-action-button icon-dark-color" type="button" aria-label="Add volume">
				<img src="${url_base}/static/img/download.svg" alt="">
			</button>
		`;
		const actionBtn = actions.querySelector('button');
		actionBtn.addEventListener('click', event => {
			event.stopPropagation();
			event.preventDefault();
			showAddWindow(volume, actionBtn);
		});
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
		if (addedVolumeId && currentPublisher) {
			showPublisherVolumes(currentPublisher);
		}
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

// Render publishers
function renderPublishers(publishers) {
	publishersGrid.innerHTML = '';
	loadMoreButton.classList.add('hidden');

	if (publishers.length === 0) {
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = 'No publishers found.';
		return;
	}

	emptyState.classList.add('hidden');

	const sorted = [...publishers].sort((a, b) => {
		if (publisherSortSelect.value === 'name-asc')
			return a.name.localeCompare(b.name);
		if (publisherSortSelect.value === 'name-desc')
			return b.name.localeCompare(a.name);
		if (publisherSortSelect.value === 'volumes-desc')
			return (b.volume_count || 0) - (a.volume_count || 0)
				|| a.name.localeCompare(b.name);
		const aMajor = MAJOR_PUBLISHERS[a.comicvine_id] ? 0 : 1;
		const bMajor = MAJOR_PUBLISHERS[b.comicvine_id] ? 0 : 1;
		if (aMajor !== bMajor) return aMajor - bMajor;
		return a.name.localeCompare(b.name);
	});

	const visible = sorted.slice(0, visiblePublisherCount);
	publisherCount.innerText = `${visible.length} of ${sorted.length}`;
	visible.forEach(publisher => {
		publishersGrid.appendChild(createPublisherCard(publisher));
	});
	if (visible.length < sorted.length)
		loadMoreButton.classList.remove('hidden');

	loadingIndicator.classList.add('hidden');
}

// Render volumes for a publisher
function renderVolumes(volumes) {
	volumesGrid.innerHTML = '';
	loadMoreButton.classList.add('hidden');

	if (volumes.length === 0) {
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = 'No volumes found for this publisher.';
		return;
	}

	emptyState.classList.add('hidden');

	const visible = volumes.slice(0, visibleVolumeCount);
	publisherCount.innerText = `${visible.length} of ${volumes.length}`;
	visible.forEach(volume => {
		volumesGrid.appendChild(createVolumeCard(volume));
	});
	if (visible.length < volumes.length)
		loadMoreButton.classList.remove('hidden');

	loadingIndicator.classList.add('hidden');
}

// Fetch publishers
function filteredPublishers() {
	if (!searchQuery) return allPublishers;
	return allPublishers.filter(publisher =>
		publisher.name.toLowerCase().includes(searchQuery.toLowerCase())
	);
}

function fetchPublishers(api_key, forceRefresh=false) {
	const requestId = ++publisherRequestId;
	loadingIndicator.classList.remove('hidden');
	emptyState.classList.add('hidden');
	publishersGrid.innerHTML = '';

	refreshButton.disabled = true;
	const params = { limit: 1000 };
	if (forceRefresh) params.force_refresh = 'true';

	fetchAPI('/publishers', api_key, params)
	.then(data => {
		if (requestId !== publisherRequestId) return;
		if (data.result) {
			allPublishers = data.result;
			visiblePublisherCount = 100;
			renderPublishers(filteredPublishers());
		} else {
			throw new Error(data.error || 'Failed to fetch publishers');
		}
	})
	.catch(error => {
		if (requestId !== publisherRequestId) return;
		console.error('Error fetching publishers:', error);
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = `Error: ${error.message || 'Failed to fetch publishers'}`;
	})
	.finally(() => {
		if (requestId === publisherRequestId)
			refreshButton.disabled = false;
	});
}

// Show volumes for a specific publisher
function showPublisherVolumes(publisher, forceRefresh=false) {
	const requestId = ++volumeRequestId;
	currentPublisher = publisher;
	visibleVolumeCount = 100;
	
	// Switch views
	publishersGrid.classList.add('hidden');
	volumesGrid.classList.remove('hidden');
	backButton.classList.remove('hidden');
	currentViewLabel.textContent = publisher.name;

	loadingIndicator.classList.remove('hidden');
	emptyState.classList.add('hidden');
	volumesGrid.innerHTML = '';
	refreshButton.disabled = true;

	usingApiKey()
	.then(api_key => {
		const params = { limit: 1000 };
		if (forceRefresh) params.force_refresh = 'true';
		return fetchAPI(
			`/publishers/${publisher.comicvine_id}/volumes`,
			api_key,
			params
		);
	})
	.then(data => {
		if (requestId !== volumeRequestId) return;
		if (data.result) {
			currentVolumes = data.result;
			renderVolumes(currentVolumes);
		} else {
			throw new Error(data.error || 'Failed to fetch volumes');
		}
	})
	.catch(error => {
		if (requestId !== volumeRequestId) return;
		console.error('Error fetching publisher volumes:', error);
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = `Error: ${error.message || 'Failed to fetch volumes'}`;
	})
	.finally(() => {
		if (requestId === volumeRequestId)
			refreshButton.disabled = false;
	});
}

// Go back to publishers view
function showPublishersView() {
	currentPublisher = null;
	
	volumesGrid.classList.add('hidden');
	publishersGrid.classList.remove('hidden');
	backButton.classList.add('hidden');
	currentViewLabel.textContent = '';
	visiblePublisherCount = 100;
	renderPublishers(filteredPublishers());
}

// Search handler
function handleSearch(e) {
	e.preventDefault();
	searchQuery = searchInput.value.trim();
	setLocalStorage({publisher_search: searchQuery});
	visiblePublisherCount = 100;
	
	if (currentPublisher) {
		// If in volumes view, go back to filtered publishers
		showPublishersView();
	} else {
		renderPublishers(filteredPublishers());
	}
}

// Clear search
function clearSearch() {
	searchInput.value = '';
	searchQuery = '';
	setLocalStorage({publisher_search: ''});
	visiblePublisherCount = 100;
	
	if (currentPublisher) {
		showPublishersView();
	} else {
		renderPublishers(allPublishers);
	}
}

// Store api_key at module level for use in showPublisherVolumes
let _apiKey = null;

// Initialize with API key
usingApiKey()
.then(api_key => {
	_apiKey = api_key;
	if (addWindowEls.form) {
		addWindowEls.form.action = 'javascript:addVolume();';
	}
	const preferences = getLocalStorage('publisher_sort', 'publisher_search');
	publisherSortSelect.value = preferences.publisher_sort || 'major';
	searchQuery = preferences.publisher_search || '';
	searchInput.value = searchQuery;

	refreshButton.addEventListener('click', () => {
		if (currentPublisher) {
			showPublisherVolumes(currentPublisher, true);
		} else {
			fetchPublishers(api_key, true);
		}
	});

	backButton.addEventListener('click', showPublishersView);
	searchForm.addEventListener('submit', handleSearch);
	clearSearchBtn.addEventListener('click', clearSearch);
	publisherSortSelect.addEventListener('change', () => {
		setLocalStorage({publisher_sort: publisherSortSelect.value});
		visiblePublisherCount = 100;
		if (!currentPublisher) renderPublishers(filteredPublishers());
	});
	loadMoreButton.addEventListener('click', () => {
		if (currentPublisher) {
			visibleVolumeCount += 100;
			renderVolumes(currentVolumes);
		} else {
			visiblePublisherCount += 100;
			renderPublishers(filteredPublishers());
		}
	});

	// Initial load
	fetchPublishers(api_key);
});
