// Releases page JavaScript

const releasesGrid = document.getElementById('releases-grid');
const loadingIndicator = document.getElementById('releases-loading');
const emptyState = document.getElementById('releases-empty');
const releaseTypeSelect = document.getElementById('release-type');
const daysSelect = document.getElementById('days-select');
const customRange = document.getElementById('custom-range');
const startDateInput = document.getElementById('release-start-date');
const endDateInput = document.getElementById('release-end-date');
const releaseSortSelect = document.getElementById('release-sort');
const discoveryProviderSelect = document.getElementById('discovery-provider');
const discoveryStatusSelect = document.getElementById('discovery-status');
const hideInLibraryCheckbox = document.getElementById('hide-in-library');
const refreshButton = document.getElementById('refresh-button');
const loadMoreButton = document.getElementById('load-more-button');
const releaseCount = document.getElementById('release-count');

let allReleases = [];
let hideInLibrary = false;
let rootFoldersLoaded = false;
let visibleReleaseCount = 100;
let releaseRequestId = 0;

function escapeReleaseText(value) {
	return String(value ?? '').replace(/[&<>'"]/g, character => ({
		'&': '&amp;',
		'<': '&lt;',
		'>': '&gt;',
		"'": '&#39;',
		'"': '&quot;'
	})[character]);
}

function safeCoverUrl(value) {
	if (!value) return `${url_base}/static/img/placeholder.svg`;
	try {
		const url = new URL(value, window.location.origin);
		if (url.protocol === 'http:' || url.protocol === 'https:')
			return url.href;
	} catch (_error) {
		return `${url_base}/static/img/placeholder.svg`;
	}
	return `${url_base}/static/img/placeholder.svg`;
}

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

function getReleaseDate(release) {
	return release.store_date || release.cover_date || release.release_date || '';
}

function sortReleases(releases) {
	const [field, direction] = releaseSortSelect.value.split('-');
	const multiplier = direction === 'desc' ? -1 : 1;
	return [...releases].sort((first, second) => {
		let firstValue;
		let secondValue;
		if (field === 'date') {
			firstValue = getReleaseDate(first);
			secondValue = getReleaseDate(second);
		} else if (field === 'publisher') {
			firstValue = first.publisher || '';
			secondValue = second.publisher || '';
		} else {
			firstValue = first.volume_title || '';
			secondValue = second.volume_title || '';
		}
		return multiplier * firstValue.localeCompare(
			secondValue,
			undefined,
			{ numeric: true, sensitivity: 'base' }
		);
	});
}

// Group releases by date
function groupByDate(releases) {
	const groups = {};
	releases.forEach(release => {
		const date = getReleaseDate(release) || 'Unknown';
		if (!groups[date]) {
			groups[date] = [];
		}
		groups[date].push(release);
	});
	return Object.entries(groups).map(
		([date, entries]) => ({ date, releases: entries })
	);
}

// Create a release card
function createReleaseCard(release, isLibraryView = false) {
	const card = document.createElement('div');
	card.className = 'release-card';
	card.dataset.cvId = release.issue_cv_id || release.external_id || '';
	card.dataset.volumeCvId = release.volume_cv_id || release.volume_id;
	card.dataset.metadataSource = release.metadata_source
		|| (release.providers || []).join(',')
		|| 'comicvine';
	const isDiscovery = release.local_status !== undefined;

	// Library view items are always in library
	if (release.in_library || isLibraryView) {
		card.classList.add('in-library');
	}

	const coverUrl = safeCoverUrl(release.cover_url);
	const volumeTitle = release.volume_title || release.series_title;
	const issueNumber = release.issue_number || release.calculated_issue_number;
	const releaseDate = getReleaseDate(release);
	const provenance = escapeReleaseText((release.providers || []).join(' · '));
	const localStatus = escapeReleaseText(
		(release.local_status || '').replaceAll('_', ' ')
	);
	const safeTitle = escapeReleaseText(volumeTitle);
	const safeIssueNumber = escapeReleaseText(issueNumber);
	const safePublisher = escapeReleaseText(release.publisher);

	card.innerHTML = `
		<img class="release-cover" src="${coverUrl}" alt="${safeTitle}" loading="lazy">
		<div class="release-info">
			<h3 class="release-title" title="${safeTitle}">${safeTitle}</h3>
			<p class="release-issue">#${safeIssueNumber}</p>
			<p class="release-date">${formatDate(releaseDate)}</p>
			${release.publisher ? `<p class="release-publisher">${safePublisher}</p>` : ''}
			<span class="release-badge in-library">In Library</span>
			${isDiscovery ? `<p class="release-provenance">${provenance}</p>` : ''}
			${isDiscovery ? `<span class="release-local-status status-${release.local_status}">${localStatus}</span>` : ''}
			${isDiscovery && release.available ? '<span class="release-availability">Available</span>' : ''}
		</div>
	`;

	if (!release.in_library && !isLibraryView && !isDiscovery) {
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
		const volumeId = release.volume_id || release.local_volume_id;
		if (volumeId) {
			// Go to volume page
			window.location.href = `${url_base}/volumes/${volumeId}`;
		} else if (isDiscovery) {
			const externalUrl = release.download_url
				|| release.provenance?.find(source => source.external_url)?.external_url;
			if (externalUrl) window.open(externalUrl, '_blank');
		} else if (release.issue_cv_id) {
			const externalUrl = release.metadata_source === 'metron'
				? `https://metron.cloud/issue/${release.issue_cv_id}/`
				: `https://comicvine.gamespot.com/issue/4000-${release.issue_cv_id}/`;
			window.open(externalUrl, '_blank');
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
	loadMoreButton.classList.add('hidden');

	const isLibraryView = releaseTypeSelect.value === 'library';

	// Filter if needed (not applicable to library view)
	let filteredReleases = releases;
	if (hideInLibrary && !isLibraryView) {
		filteredReleases = releases.filter(r => !r.in_library);
	}
	filteredReleases = sortReleases(filteredReleases);
	const visibleReleases = filteredReleases.slice(0, visibleReleaseCount);
	releaseCount.innerText = `${visibleReleases.length} of ${filteredReleases.length}`;

	if (filteredReleases.length === 0) {
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = isLibraryView 
			? 'No upcoming releases found in your library.'
			: 'No releases found for this period.';
		return;
	}

	emptyState.classList.add('hidden');

	if (releaseSortSelect.value.startsWith('date-')) {
		groupByDate(visibleReleases).forEach(group => {
			const header = document.createElement('div');
			header.className = 'date-group';
			header.innerHTML = `<h2>${formatDate(group.date)}</h2>`;
			releasesGrid.appendChild(header);

			group.releases.forEach(release => {
				releasesGrid.appendChild(createReleaseCard(release, isLibraryView));
			});
		});
	} else {
		visibleReleases.forEach(release => {
			releasesGrid.appendChild(createReleaseCard(release, isLibraryView));
		});
	}

	if (visibleReleases.length < filteredReleases.length) {
		loadMoreButton.classList.remove('hidden');
	}

	loadingIndicator.classList.add('hidden');
}

function formatInputDate(date) {
	const year = date.getFullYear();
	const month = String(date.getMonth() + 1).padStart(2, '0');
	const day = String(date.getDate()).padStart(2, '0');
	return `${year}-${month}-${day}`;
}

function ensureCustomRange() {
	if (startDateInput.value && endDateInput.value) return;
	const start = new Date();
	const end = new Date();
	const isUpcoming = releaseTypeSelect.value !== 'recent';
	if (isUpcoming)
		end.setDate(end.getDate() + 30);
	else
		start.setDate(start.getDate() - 30);
	startDateInput.value = formatInputDate(start);
	endDateInput.value = formatInputDate(end);
	setLocalStorage({
		release_custom_start: startDateInput.value,
		release_custom_end: endDateInput.value
	});
}

function validateCustomRange() {
	if (!startDateInput.value || !endDateInput.value) return false;
	const start = new Date(`${startDateInput.value}T00:00:00Z`);
	const end = new Date(`${endDateInput.value}T00:00:00Z`);
	const days = (end - start) / 86400000;
	let message = '';
	if (days < 0)
		message = 'End date must be after start date';
	else if (days > 365)
		message = 'Date range must be 365 days or less';
	endDateInput.setCustomValidity(message);
	if (message) endDateInput.reportValidity();
	return message === '';
}

// Fetch releases from API
function fetchReleases(api_key, forceRefresh=false) {
	const selectedRange = daysSelect.value;
	if (selectedRange === 'custom') {
		ensureCustomRange();
		if (!validateCustomRange()) return Promise.resolve();
	}
	const requestId = ++releaseRequestId;
	loadingIndicator.classList.remove('hidden');
	emptyState.classList.add('hidden');
	releasesGrid.innerHTML = '';
	loadMoreButton.classList.add('hidden');
	refreshButton.disabled = true;

	const releaseType = releaseTypeSelect.value;

	let endpoint;
	let params = { limit: 5000 };
	if (releaseType === 'discovery') {
		endpoint = '/releases/discovery';
		if (selectedRange === 'custom') {
			params.start_date = startDateInput.value;
			params.end_date = endDateInput.value;
		} else {
			const end = new Date();
			const start = new Date();
			start.setDate(start.getDate() - parseInt(selectedRange));
			params.start_date = formatInputDate(start);
			params.end_date = formatInputDate(end);
		}
		params.per_page = 500;
		if (discoveryProviderSelect.value)
			params.provider = discoveryProviderSelect.value;
		if (discoveryStatusSelect.value)
			params.local_status = discoveryStatusSelect.value;
	} else if (selectedRange === 'custom') {
		ensureCustomRange();
		endpoint = releaseType === 'library'
			? '/releases/library/upcoming'
			: '/releases/new';
		params = {
			...params,
			start_date: startDateInput.value,
			end_date: endDateInput.value
		};
	} else if (releaseType === 'upcoming') {
		endpoint = '/releases/upcoming';
		params.days_ahead = parseInt(selectedRange);
	} else if (releaseType === 'library') {
		endpoint = '/releases/library/upcoming';
		params.days_ahead = parseInt(selectedRange);
	} else {
		endpoint = '/releases/recent';
		params.days_back = parseInt(selectedRange);
	}
	if (forceRefresh && releaseType !== 'library')
		params.force_refresh = 'true';

	fetchAPI(endpoint, api_key, params)
	.then(data => {
		if (requestId !== releaseRequestId) return;
		if (data.result) {
			allReleases = releaseType === 'discovery'
				? data.result.items.map(release => ({
					...release,
					in_library: release.local_status !== 'not_in_library',
					volume_id: release.local_volume_id
				}))
				: data.result;
			visibleReleaseCount = 100;
			renderReleases(allReleases);
		} else {
			throw new Error(data.error || 'Failed to fetch releases');
		}
	})
	.catch(error => {
		if (requestId !== releaseRequestId) return;
		console.error('Error fetching releases:', error);
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = `Error: ${error.message || 'Failed to fetch releases'}`;
	})
	.finally(() => {
		if (requestId === releaseRequestId)
			refreshButton.disabled = false;
	});
}

// Update days label based on release type
function updateDaysLabel() {
	const options = daysSelect.querySelectorAll('option');
	const releaseType = releaseTypeSelect.value;
	const isUpcoming = releaseType === 'upcoming' || releaseType === 'library';
	const isDiscovery = releaseType === 'discovery';
	
	options.forEach(opt => {
		if (opt.value === 'custom') {
			opt.textContent = 'Custom Dates';
			return;
		}
		const days = opt.value;
		opt.textContent = isUpcoming ? `Next ${days} Days` : `Last ${days} Days`;
	});
	customRange.classList.toggle('hidden', daysSelect.value !== 'custom');

	// Hide "Hide In Library" checkbox for library view
	const hideInLibraryContainer = hideInLibraryCheckbox.closest('.filter-checkbox');
	if (hideInLibraryContainer) {
		hideInLibraryContainer.style.display = releaseType === 'library' ? 'none' : 'flex';
	}
	discoveryProviderSelect.classList.toggle('hidden', !isDiscovery);
	discoveryStatusSelect.classList.toggle('hidden', !isDiscovery);
}

// Initialize with API key
usingApiKey()
.then(api_key => {
	if (addWindowEls.form) {
		addWindowEls.form.action = 'javascript:addVolume();';
	}
	const preferences = getLocalStorage(
		'release_type',
		'release_range',
		'release_sort',
		'release_custom_start',
		'release_custom_end',
		'release_hide_in_library',
		'release_provider',
		'release_local_status'
	);
	releaseTypeSelect.value = preferences.release_type || 'recent';
	daysSelect.value = preferences.release_range || '30';
	releaseSortSelect.value = preferences.release_sort || 'date-desc';
	startDateInput.value = preferences.release_custom_start || '';
	endDateInput.value = preferences.release_custom_end || '';
	hideInLibrary = Boolean(preferences.release_hide_in_library);
	hideInLibraryCheckbox.checked = hideInLibrary;
	discoveryProviderSelect.value = preferences.release_provider || '';
	discoveryStatusSelect.value = preferences.release_local_status || '';

	releaseTypeSelect.addEventListener('change', () => {
		const isUpcoming = (
			releaseTypeSelect.value === 'upcoming'
			|| releaseTypeSelect.value === 'library'
		);
		if (releaseSortSelect.value.startsWith('date-'))
			releaseSortSelect.value = isUpcoming ? 'date-asc' : 'date-desc';
		setLocalStorage({
			release_type: releaseTypeSelect.value,
			release_sort: releaseSortSelect.value
		});
		updateDaysLabel();
		fetchReleases(api_key);
	});

	daysSelect.addEventListener('change', () => {
		setLocalStorage({release_range: daysSelect.value});
		updateDaysLabel();
		if (daysSelect.value === 'custom') ensureCustomRange();
		fetchReleases(api_key);
	});

	[startDateInput, endDateInput].forEach(input => {
		input.addEventListener('change', () => {
			if (!validateCustomRange()) return;
			setLocalStorage({
				release_custom_start: startDateInput.value,
				release_custom_end: endDateInput.value
			});
			fetchReleases(api_key);
		});
	});

	releaseSortSelect.addEventListener('change', () => {
		setLocalStorage({release_sort: releaseSortSelect.value});
		visibleReleaseCount = 100;
		renderReleases(allReleases);
	});

	hideInLibraryCheckbox.addEventListener('change', (e) => {
		hideInLibrary = e.target.checked;
		setLocalStorage({release_hide_in_library: hideInLibrary});
		visibleReleaseCount = 100;
		renderReleases(allReleases);
	});

	discoveryProviderSelect.addEventListener('change', () => {
		setLocalStorage({release_provider: discoveryProviderSelect.value});
		fetchReleases(api_key);
	});
	discoveryStatusSelect.addEventListener('change', () => {
		setLocalStorage({release_local_status: discoveryStatusSelect.value});
		fetchReleases(api_key);
	});

	refreshButton.addEventListener('click', () => fetchReleases(api_key, true));
	loadMoreButton.addEventListener('click', () => {
		visibleReleaseCount += 100;
		renderReleases(allReleases);
	});

	// Initial load
	updateDaysLabel();
	fetchReleases(api_key);
});
