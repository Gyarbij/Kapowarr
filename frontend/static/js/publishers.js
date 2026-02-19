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
let currentPublisher = null;
let searchQuery = '';

// Create a publisher card
function createPublisherCard(publisher) {
	const card = document.createElement('div');
	card.className = 'publisher-card';
	card.dataset.cvId = publisher.comicvine_id;

	// Mark major publishers
	if (MAJOR_PUBLISHERS[publisher.comicvine_id]) {
		card.classList.add('major');
	}

	card.innerHTML = `
		<h3 class="publisher-name" title="${publisher.name}">${publisher.name}</h3>
		<p class="publisher-volumes">${publisher.volume_count || 'Browse'} volumes</p>
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

	return card;
}

// Render publishers
function renderPublishers(publishers) {
	publishersGrid.innerHTML = '';

	if (publishers.length === 0) {
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = 'No publishers found.';
		return;
	}

	emptyState.classList.add('hidden');

	// Sort: major publishers first, then alphabetically
	const sorted = [...publishers].sort((a, b) => {
		const aMajor = MAJOR_PUBLISHERS[a.comicvine_id] ? 0 : 1;
		const bMajor = MAJOR_PUBLISHERS[b.comicvine_id] ? 0 : 1;
		if (aMajor !== bMajor) return aMajor - bMajor;
		return a.name.localeCompare(b.name);
	});

	sorted.forEach(publisher => {
		publishersGrid.appendChild(createPublisherCard(publisher));
	});

	loadingIndicator.classList.add('hidden');
}

// Render volumes for a publisher
function renderVolumes(volumes) {
	volumesGrid.innerHTML = '';

	if (volumes.length === 0) {
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = 'No volumes found for this publisher.';
		return;
	}

	emptyState.classList.add('hidden');

	volumes.forEach(volume => {
		volumesGrid.appendChild(createVolumeCard(volume));
	});

	loadingIndicator.classList.add('hidden');
}

// Fetch publishers
function fetchPublishers(api_key) {
	loadingIndicator.classList.remove('hidden');
	emptyState.classList.add('hidden');
	publishersGrid.innerHTML = '';

	fetchAPI('/publishers', api_key, { limit: 100 })
	.then(data => {
		if (data.result) {
			allPublishers = data.result;
			
			// Apply search filter
			let filtered = allPublishers;
			if (searchQuery) {
				filtered = allPublishers.filter(p => 
					p.name.toLowerCase().includes(searchQuery.toLowerCase())
				);
			}
			
			renderPublishers(filtered);
		} else {
			throw new Error(data.error || 'Failed to fetch publishers');
		}
	})
	.catch(error => {
		console.error('Error fetching publishers:', error);
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = `Error: ${error.message || 'Failed to fetch publishers'}`;
	});
}

// Show volumes for a specific publisher
function showPublisherVolumes(publisher) {
	currentPublisher = publisher;
	
	// Switch views
	publishersGrid.classList.add('hidden');
	volumesGrid.classList.remove('hidden');
	backButton.classList.remove('hidden');
	currentViewLabel.textContent = publisher.name;

	loadingIndicator.classList.remove('hidden');
	emptyState.classList.add('hidden');
	volumesGrid.innerHTML = '';

	fetchAPI(`/publishers/${publisher.comicvine_id}/volumes`, _apiKey, { limit: 100 })
	.then(data => {
		if (data.result) {
			renderVolumes(data.result);
		} else {
			throw new Error(data.error || 'Failed to fetch volumes');
		}
	})
	.catch(error => {
		console.error('Error fetching publisher volumes:', error);
		loadingIndicator.classList.add('hidden');
		emptyState.classList.remove('hidden');
		emptyState.querySelector('p').textContent = `Error: ${error.message || 'Failed to fetch volumes'}`;
	});
}

// Go back to publishers view
function showPublishersView() {
	currentPublisher = null;
	
	volumesGrid.classList.add('hidden');
	publishersGrid.classList.remove('hidden');
	backButton.classList.add('hidden');
	currentViewLabel.textContent = '';
	
	// Re-render with current search
	let filtered = allPublishers;
	if (searchQuery) {
		filtered = allPublishers.filter(p => 
			p.name.toLowerCase().includes(searchQuery.toLowerCase())
		);
	}
	renderPublishers(filtered);
}

// Search handler
function handleSearch(e) {
	e.preventDefault();
	searchQuery = searchInput.value.trim();
	
	if (currentPublisher) {
		// If in volumes view, go back to filtered publishers
		showPublishersView();
	} else {
		// Filter publishers
		let filtered = allPublishers;
		if (searchQuery) {
			filtered = allPublishers.filter(p => 
				p.name.toLowerCase().includes(searchQuery.toLowerCase())
			);
		}
		renderPublishers(filtered);
	}
}

// Clear search
function clearSearch() {
	searchInput.value = '';
	searchQuery = '';
	
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

	refreshButton.addEventListener('click', () => {
		if (currentPublisher) {
			showPublisherVolumes(currentPublisher);
		} else {
			fetchPublishers(api_key);
		}
	});

	backButton.addEventListener('click', showPublishersView);
	searchForm.addEventListener('submit', handleSearch);
	clearSearchBtn.addEventListener('click', clearSearch);

	// Initial load
	fetchPublishers(api_key);
});
