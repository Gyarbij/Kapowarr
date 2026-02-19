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
