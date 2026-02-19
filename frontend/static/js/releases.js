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
	// Sort dates descending for recent, ascending for upcoming
	const sortedDates = Object.keys(groups).sort((a, b) => {
		if (a === 'Unknown') return 1;
		if (b === 'Unknown') return -1;
		const isUpcoming = releaseTypeSelect.value === 'upcoming';
		return isUpcoming ? a.localeCompare(b) : b.localeCompare(a);
	});
	return sortedDates.map(date => ({ date, releases: groups[date] }));
}

// Create a release card
function createReleaseCard(release) {
	const card = document.createElement('div');
	card.className = 'release-card';
	card.dataset.cvId = release.issue_cv_id;
	card.dataset.volumeCvId = release.volume_cv_id;

	if (release.in_library) {
		card.classList.add('in-library');
	}

	const coverUrl = release.cover_url || `${url_base}/static/img/placeholder.svg`;

	card.innerHTML = `
		<img class="release-cover" src="${coverUrl}" alt="${release.volume_title}" loading="lazy">
		<div class="release-info">
			<h3 class="release-title" title="${release.volume_title}">${release.volume_title}</h3>
			<p class="release-issue">#${release.issue_number}</p>
			<p class="release-date">${formatDate(release.store_date || release.cover_date)}</p>
			<span class="release-badge in-library">In Library</span>
		</div>
	`;

	// Click to view on ComicVine or add to library
	card.addEventListener('click', () => {
		if (release.in_library && release.volume_id) {
			// Go to volume page
			window.location.href = `${url_base}/volumes/${release.volume_id}`;
		} else {
			// Open ComicVine page
			window.open(`https://comicvine.gamespot.com/issue/4000-${release.issue_cv_id}/`, '_blank');
		}
	});

	return card;
}

// Render releases to grid
function renderReleases(releases) {
	releasesGrid.innerHTML = '';

	// Filter if needed
	let filteredReleases = releases;
	if (hideInLibrary) {
		filteredReleases = releases.filter(r => !r.in_library);
	}

	if (filteredReleases.length === 0) {
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
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
			releasesGrid.appendChild(createReleaseCard(release));
		});
	});

	loadingIndicator.classList.add('hidden');
}

// Fetch releases from API
async function fetchReleases() {
	loadingIndicator.classList.remove('hidden');
	emptyState.classList.add('hidden');
	releasesGrid.innerHTML = '';

	const releaseType = releaseTypeSelect.value;
	const days = parseInt(daysSelect.value);

	let endpoint;
	if (releaseType === 'upcoming') {
		endpoint = `${url_base}/api/releases/upcoming?days_ahead=${days}`;
	} else {
		endpoint = `${url_base}/api/releases/recent?days_back=${days}`;
	}

	try {
		const response = await fetch(endpoint, {
			headers: getAuthHeaders()
		});
		const data = await response.json();

		if (data.result) {
			allReleases = data.result;
			renderReleases(allReleases);
		} else {
			throw new Error(data.error || 'Failed to fetch releases');
		}
	} catch (error) {
		console.error('Error fetching releases:', error);
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = `Error: ${error.message}`;
	}
}

// Update days label based on release type
function updateDaysLabel() {
	const options = daysSelect.querySelectorAll('option');
	const isUpcoming = releaseTypeSelect.value === 'upcoming';
	
	options.forEach(opt => {
		const days = opt.value;
		opt.textContent = isUpcoming ? `Next ${days} Days` : `Last ${days} Days`;
	});
}

// Event listeners
releaseTypeSelect.addEventListener('change', () => {
	updateDaysLabel();
	fetchReleases();
});

daysSelect.addEventListener('change', fetchReleases);

hideInLibraryCheckbox.addEventListener('change', (e) => {
	hideInLibrary = e.target.checked;
	renderReleases(allReleases);
});

refreshButton.addEventListener('click', fetchReleases);

// Initial load
updateDaysLabel();
fetchReleases();
