// Releases page JavaScript

const releasesGrid = document.getElementById('releases-grid');
const loadingIndicator = document.getElementById('releases-loading');
const emptyState = document.getElementById('releases-empty');
const releaseTypeSelect = document.getElementById('release-type');
const daysSelect = document.getElementById('days-select');
const hideInLibraryCheckbox = document.getElementById('hide-in-library');
const refreshButton = document.getElementById('refresh-button');

// Pre-built card template
const cardTemplate = document.querySelector('.release-card');
if (cardTemplate) {
	cardTemplate.remove();
}

let allReleases = [];
let hideInLibrary = false;
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

// Format date for display
function formatDate(dateStr) {
	if (!dateStr) return 'Unknown';
	const date = new Date(dateStr);
	const options = { month: 'short', day: 'numeric', year: 'numeric' };
	return date.toLocaleDateString('en-US', options);
}

// Group releases by date
function groupByDate(releases) {
	const groups = {};
	releases.forEach(release => {
		const date = release.store_date || release.cover_date || 'Unknown';
		if (!groups[date]) {
			groups[date] = [];
		}
		groups[date].push(release);
	});
	// Sort dates descending for recent, ascending for upcoming/library
	const sortedDates = Object.keys(groups).sort((a, b) => {
		if (a === 'Unknown') return 1;
		if (b === 'Unknown') return -1;
		const releaseType = releaseTypeSelect.value;
		const isUpcoming = releaseType === 'upcoming' || releaseType === 'library';
		return isUpcoming ? a.localeCompare(b) : b.localeCompare(a);
	});
	return sortedDates.map(date => ({ date, releases: groups[date] }));
}

// Create a release card
function createReleaseCard(release, isLibraryView = false) {
	const card = document.createElement('div');
	card.className = 'release-card';
	card.dataset.cvId = release.issue_cv_id;
	card.dataset.volumeCvId = release.volume_cv_id || release.volume_id;

	// Library view items are always in library
	if (release.in_library || isLibraryView) {
		card.classList.add('in-library');
	}

	const coverUrl = release.cover_url || `${url_base}/static/img/placeholder.svg`;
	const volumeTitle = release.volume_title;
	const issueNumber = release.issue_number;
	const releaseDate = release.store_date || release.cover_date;

	card.innerHTML = `
		<img class="release-cover" src="${coverUrl}" alt="${volumeTitle}" loading="lazy">
		<div class="release-info">
			<h3 class="release-title" title="${volumeTitle}">${volumeTitle}</h3>
			<p class="release-issue">#${issueNumber}</p>
			<p class="release-date">${formatDate(releaseDate)}</p>
			${release.publisher ? `<p class="release-publisher">${release.publisher}</p>` : ''}
			<span class="release-badge in-library">In Library</span>
		</div>
	`;

	if (!release.in_library && !isLibraryView) {
		const actions = document.createElement('div');
		actions.className = 'release-actions';
		actions.innerHTML = `
			<button class="release-action-button icon-dark-color" type="button" aria-label="Add volume">
				<img src="${url_base}/static/img/download.svg" alt="">
			</button>
		`;
		const actionBtn = actions.querySelector('button');
		actionBtn.addEventListener('click', event => {
			event.stopPropagation();
			showAddWindow(release, actionBtn);
		});
		card.appendChild(actions);
	}

	// Click to view volume or ComicVine
	card.addEventListener('click', () => {
		const volumeId = release.volume_id;
		if (volumeId) {
			// Go to volume page
			window.location.href = `${url_base}/volumes/${volumeId}`;
		} else if (release.issue_cv_id) {
			// Open ComicVine page
			window.open(`https://comicvine.gamespot.com/issue/4000-${release.issue_cv_id}/`, '_blank');
		}
	});

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

function showAddWindow(release, actionButton) {
	if (!addWindowEls.form) return;
	if (actionButton) actionButton.disabled = true;

	usingApiKey()
	.then(api_key => Promise.all([
		ensureRootFolders(api_key),
		fetchAPI('/volumes/metadata', api_key, { comicvine_id: release.volume_cv_id })
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
		if (addedVolumeId) {
			allReleases = allReleases.map(release => {
				if (release.volume_cv_id === data.comicvine_id) {
					return {
						...release,
						in_library: true,
						volume_id: addedVolumeId
					};
				}
				return release;
			});
			renderReleases(allReleases);
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

// Render releases to grid
function renderReleases(releases) {
	releasesGrid.innerHTML = '';

	const isLibraryView = releaseTypeSelect.value === 'library';

	// Filter if needed (not applicable to library view)
	let filteredReleases = releases;
	if (hideInLibrary && !isLibraryView) {
		filteredReleases = releases.filter(r => !r.in_library);
	}

	if (filteredReleases.length === 0) {
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = isLibraryView 
			? 'No upcoming releases found in your library.'
			: 'No releases found for this period.';
		return;
	}

	emptyState.classList.add('hidden');

	// Group by date
	const groups = groupByDate(filteredReleases);

	groups.forEach(group => {
		// Date header
		const header = document.createElement('div');
		header.className = 'date-group';
		header.innerHTML = `<h2>${formatDate(group.date)}</h2>`;
		releasesGrid.appendChild(header);

		// Release cards
		group.releases.forEach(release => {
			releasesGrid.appendChild(createReleaseCard(release, isLibraryView));
		});
	});

	loadingIndicator.classList.add('hidden');
}

// Fetch releases from API
function fetchReleases(api_key) {
	loadingIndicator.classList.remove('hidden');
	emptyState.classList.add('hidden');
	releasesGrid.innerHTML = '';

	const releaseType = releaseTypeSelect.value;
	const days = parseInt(daysSelect.value);

	let endpoint;
	let params = {};
	if (releaseType === 'upcoming') {
		endpoint = '/releases/upcoming';
		params = { days_ahead: days };
	} else if (releaseType === 'library') {
		endpoint = '/releases/library/upcoming';
		params = { days_ahead: days };
	} else {
		endpoint = '/releases/recent';
		params = { days_back: days };
	}

	fetchAPI(endpoint, api_key, params)
	.then(data => {
		if (data.result) {
			allReleases = data.result;
			renderReleases(allReleases);
		} else {
			throw new Error(data.error || 'Failed to fetch releases');
		}
	})
	.catch(error => {
		console.error('Error fetching releases:', error);
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = `Error: ${error.message || 'Failed to fetch releases'}`;
	});
}

// Update days label based on release type
function updateDaysLabel() {
	const options = daysSelect.querySelectorAll('option');
	const releaseType = releaseTypeSelect.value;
	const isUpcoming = releaseType === 'upcoming' || releaseType === 'library';
	
	options.forEach(opt => {
		const days = opt.value;
		opt.textContent = isUpcoming ? `Next ${days} Days` : `Last ${days} Days`;
	});

	// Hide "Hide In Library" checkbox for library view
	const hideInLibraryContainer = hideInLibraryCheckbox.closest('.filter-checkbox');
	if (hideInLibraryContainer) {
		hideInLibraryContainer.style.display = releaseType === 'library' ? 'none' : 'flex';
	}
}

// Initialize with API key
usingApiKey()
.then(api_key => {
	if (addWindowEls.form) {
		addWindowEls.form.action = 'javascript:addVolume();';
	}
	ensureRootFolders(api_key);

	releaseTypeSelect.addEventListener('change', () => {
		updateDaysLabel();
		fetchReleases(api_key);
	});

	daysSelect.addEventListener('change', () => fetchReleases(api_key));

	hideInLibraryCheckbox.addEventListener('change', (e) => {
		hideInLibrary = e.target.checked;
		renderReleases(allReleases);
	});

	refreshButton.addEventListener('click', () => fetchReleases(api_key));

	// Initial load
	updateDaysLabel();
	fetchReleases(api_key);
});
