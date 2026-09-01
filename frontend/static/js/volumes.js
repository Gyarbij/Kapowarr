const library_els = {
	pages: {
		loading: document.querySelector('#loading-library'),
		empty: document.querySelector('#empty-library'),
		view: document.querySelector('#library-container'),
	},
	views: {
		list: document.querySelector('#list-library'),
		table: document.querySelector('#table-library'),
		table_container: document.querySelector('#table-container')
	},
	view_options: {
		sort: document.querySelector('#sort-button'),
		view: document.querySelector('#view-button'),
		status: document.querySelector('#status-filter'),
		date: document.querySelector('#date-filter'),
		publisher: document.querySelector('#publisher-filter'),
		description: document.querySelector('#description-filter')
	},
	task_buttons: {
		update_all: document.querySelector('#updateall-button'),
		search_all: document.querySelector('#searchall-button'),
		refresh_search: document.querySelector('#refreshsearch-button')
	},
	search: {
		clear: document.querySelector('#clear-search'),
		container: document.querySelector('#search-container'),
		input: document.querySelector('#search-input')
	},
	stats: {
		volume_count: document.querySelector('#volume-count'),
		volume_monitored_count: document.querySelector('#volume-monitored-count'),
		volume_unmonitored_count: document.querySelector('#volume-unmonitored-count'),
		issue_count: document.querySelector('#issue-count'),
		issue_download_count: document.querySelector('#issue-download-count'),
		file_count: document.querySelector('#file-count'),
		total_file_size: document.querySelector('#total-file-size')
	},
	mass_edit: {
		bar: document.querySelector('.action-bar'),
		button: document.querySelector('#massedit-button'),
		toggle: document.querySelector('#massedit-toggle'),
		select_all: document.querySelector('#selectall-input'),
		cancel: document.querySelector('#cancel-massedit'),
		progress: document.querySelector('#massedit-progress'),
		scope: document.querySelector('#library-scope')
	},
	pagination: {
		container: document.querySelector('#pagination-controls'),
		first: document.querySelector('#page-first'),
		prev: document.querySelector('#page-prev'),
		info: document.querySelector('#page-info'),
		next: document.querySelector('#page-next'),
		last: document.querySelector('#page-last'),
		size: document.querySelector('#page-size')
	}
};

const pre_build_els = {
	list_entry: document.querySelector('.pre-build-els .list-entry'),
	table_entry: document.querySelector('.pre-build-els .table-entry')
};

const state = {
	displayMode: 'virtual_scroll',
	currentVolumes: [],
	selectedVolumeIds: new Set(),
	virtualScroller: null
};

const paginationState = {
	currentPage: 1,
	totalPages: 1,
	totalItems: 0,
	pageSize: 50
};

let libraryRequestId = 0;
let activeLibraryRequestKey = null;
let activeLibraryRequest = null;

function showLibraryPage(el) {
	hide(Object.values(library_els.pages), [el]);
}

function getActiveView() {
	return library_els.view_options.view.value;
}

function getCoverURL(id, api_key) {
	const size = getActiveView() === 'posters' ? 'thumb' : 'full';
	return `${url_base}/api/volumes/${id}/cover?api_key=${api_key}&size=${size}`;
}

function setProgressBar(entry, downloaded_count, total_count, entryType, monitored) {
	const safeTotal = Math.max(total_count, 0);
	const safeDownloaded = Math.min(downloaded_count, safeTotal);
	const progress = safeTotal > 0 ? safeDownloaded / safeTotal * 100 : 0;

	const barClass = entryType === 'list' ? '.list-prog-bar' : '.table-prog-bar';
	const numberClass = entryType === 'list' ? '.list-prog-num' : '.table-prog-num';
	const bar = entry.querySelector(barClass);
	entry.querySelector(numberClass).innerText = `${safeDownloaded}/${safeTotal}`;
	bar.style.width = `${progress}%`;

	if (progress === 100)
		bar.style.backgroundColor = 'var(--success-color)';
	else if (monitored)
		bar.style.backgroundColor = 'var(--accent-color)';
	else
		bar.style.backgroundColor = 'var(--error-color)';
}

function createListEntry(volume, api_key) {
	const list_entry = pre_build_els.list_entry.cloneNode(true);
	list_entry.classList.add(`vol-${volume.id}`);
	list_entry.ariaLabel =
		`View the volume ${volume.title} (${volume.year}) Volume ${volume.volume_number}`;
	list_entry.href = `${url_base}/volumes/${volume.id}`;
	list_entry.querySelector('.list-img').src = getCoverURL(volume.id, api_key);

	const list_title = list_entry.querySelector('.list-title');
	list_title.innerText = `${volume.title} (${volume.year})`;
	list_title.title = list_title.innerText;
	list_entry.querySelector('.list-volume').innerText = `Volume ${volume.volume_number}`;

	if (volume.monitored)
		list_entry.setAttribute('monitored', '');

	setProgressBar(
		list_entry,
		volume.issues_downloaded_monitored,
		volume.issue_count_monitored,
		'list',
		volume.monitored
	);

	return list_entry;
}

function toggleMonitored(volume_id, monitored, api_key) {
	sendAPI('PUT', `/volumes/${volume_id}`, api_key, {}, { monitored })
		.then(() => fetchLibrary(api_key));
}

function createTableEntry(volume, api_key) {
	const table_entry = pre_build_els.table_entry.cloneNode(true);
	table_entry.classList.add(`vol-${volume.id}`);
	table_entry.dataset.id = volume.id;
	table_entry.ariaLabel =
		`View the volume ${volume.title} (${volume.year}) Volume ${volume.volume_number}`;

	const checkbox = table_entry.querySelector('input[type="checkbox"]');
	checkbox.checked = state.selectedVolumeIds.has(volume.id);
	checkbox.onchange = () => {
		if (checkbox.checked)
			state.selectedVolumeIds.add(volume.id);
		else
			state.selectedVolumeIds.delete(volume.id);
	};

	const table_link = table_entry.querySelector('.table-link');
	table_link.href = `${url_base}/volumes/${volume.id}`;
	table_link.innerText = volume.title;

	table_entry.querySelector('.table-year').innerText = volume.year;
	table_entry.querySelector('.table-volume').innerText = `Volume ${volume.volume_number}`;

	const monitored_button = table_entry.querySelector('.table-monitored');
	monitored_button.onclick = () => toggleMonitored(volume.id, !volume.monitored, api_key);
	if (volume.monitored)
		setIcon(monitored_button, icons.monitored, 'Monitored');
	else
		setIcon(monitored_button, icons.unmonitored, 'Unmonitored');

	setProgressBar(
		table_entry,
		volume.issues_downloaded_monitored,
		volume.issue_count_monitored,
		'table',
		volume.monitored
	);

	return table_entry;
}

function clearLibraryView() {
	if (state.virtualScroller !== null) {
		state.virtualScroller.destroy();
		state.virtualScroller = null;
	}

	library_els.views.list.querySelectorAll('.list-entry').forEach(entry => entry.remove());
	library_els.views.table.innerHTML = '';
}

function renderStatic(volumes, api_key) {
	const activeView = getActiveView();
	if (activeView === 'posters') {
		let spaceTaker = library_els.views.list.querySelector('.space-taker');
		if (spaceTaker === null) {
			spaceTaker = document.createElement('div');
			spaceTaker.classList.add('space-taker');
			library_els.views.list.appendChild(spaceTaker);
		}

		const fragment = document.createDocumentFragment();
		volumes.forEach(volume => fragment.appendChild(createListEntry(volume, api_key)));
		library_els.views.list.insertBefore(
			fragment,
			spaceTaker
		);
	} else {
		const fragment = document.createDocumentFragment();
		volumes.forEach(volume => fragment.appendChild(createTableEntry(volume, api_key)));
		library_els.views.table.appendChild(fragment);
	}
}

function renderVirtual(volumes, api_key) {
	const activeView = getActiveView();
	if (activeView === 'posters') {
		state.virtualScroller = new VirtualScroller({
			container: library_els.views.list,
			data: volumes,
			mode: 'posters',
			buffer: 10,
			posterWidth: 140,
			posterHeight: 280,
			posterGap: 16,
			renderItem: volume => createListEntry(volume, api_key)
		});
	} else {
		state.virtualScroller = new VirtualScroller({
			container: library_els.views.table,
			data: volumes,
			mode: 'table',
			buffer: 10,
			tableRowHeight: 56,
			renderItem: volume => createTableEntry(volume, api_key)
		});
	}
}

function renderLibrary(volumes, api_key) {
	clearLibraryView();
	if (state.displayMode === 'virtual_scroll')
		renderVirtual(volumes, api_key);
	else
		renderStatic(volumes, api_key);
}

function updatePaginationControls() {
	const isPaginationMode = state.displayMode === 'pagination';
	if (!isPaginationMode || paginationState.totalItems === 0) {
		library_els.pagination.container.classList.add('hidden');
		return;
	}

	library_els.pagination.container.classList.remove('hidden');
	library_els.pagination.info.innerText =
		`Page ${paginationState.currentPage} of ${paginationState.totalPages} (${paginationState.totalItems} volumes)`;

	library_els.pagination.first.disabled =
	library_els.pagination.prev.disabled =
		paginationState.currentPage <= 1;

	library_els.pagination.next.disabled =
	library_els.pagination.last.disabled =
		paginationState.currentPage >= paginationState.totalPages;
}

function getVolumeParams() {
	const params = {
		sort: library_els.view_options.sort.value,
		status_filter: library_els.view_options.status.value,
		date_filter: library_els.view_options.date.value,
		minimal: 'true'
	};

	const query = library_els.search.input.value;
	if (query !== '')
		params.query = query;

	const publisher = library_els.view_options.publisher.value;
	if (publisher !== '')
		params.publisher = publisher;

	const has_description = library_els.view_options.description.value;
	if (has_description !== '')
		params.has_description = has_description;

	if (state.displayMode === 'pagination') {
		params.page = paginationState.currentPage;
		params.per_page = paginationState.pageSize;
	} else {
		params.per_page = 0;
	}

	return params;
}

function fetchLibrary(api_key) {
	const params = getVolumeParams();
	const requestKey = JSON.stringify(params);
	if (activeLibraryRequest && activeLibraryRequestKey === requestKey)
		return activeLibraryRequest;

	const requestId = ++libraryRequestId;
	activeLibraryRequestKey = requestKey;
	library_els.mass_edit.progress.innerText = '';
	showLibraryPage(library_els.pages.loading);

	activeLibraryRequest = fetchAPI('/volumes', api_key, params)
		.then(json => {
			if (requestId !== libraryRequestId) return;
			const result = json.result;
			state.currentVolumes = result.volumes;
			const openIssues = result.volumes.reduce(
				(total, volume) => total + Math.max(
					0,
					volume.issue_count_monitored
						- volume.issues_downloaded_monitored
				),
				0
			);
			library_els.mass_edit.scope.innerText =
				`${result.volumes.length} visible volumes · ${openIssues} monitored issues missing`;

			paginationState.totalItems = result.total || 0;
			if (state.displayMode === 'pagination') {
				paginationState.currentPage = result.page || paginationState.currentPage;
				paginationState.totalPages = result.total_pages || 1;
			} else {
				paginationState.currentPage = 1;
				paginationState.totalPages = 1;
			}

			if (result.total === 0) {
				showLibraryPage(library_els.pages.empty);
				updatePaginationControls();
				return;
			}

			renderLibrary(result.volumes, api_key);
			showLibraryPage(library_els.pages.view);
			updatePaginationControls();
		})
		.finally(() => {
			if (requestId === libraryRequestId) {
				activeLibraryRequest = null;
				activeLibraryRequestKey = null;
			}
		});
	return activeLibraryRequest;
}

function goToPage(page, api_key) {
	paginationState.currentPage = Math.max(1, Math.min(page, paginationState.totalPages));
	fetchLibrary(api_key);
}

function searchLibrary() {
	paginationState.currentPage = 1;
	usingApiKey().then(api_key => fetchLibrary(api_key));
}

function clearSearch(api_key) {
	library_els.search.input.value = '';
	paginationState.currentPage = 1;
	fetchLibrary(api_key);
}

function fetchStats(api_key) {
	fetchAPI('/volumes/stats', api_key)
		.then(json => {
			library_els.stats.volume_count.innerText = json.result.volumes;
			library_els.stats.volume_monitored_count.innerText = json.result.monitored;
			library_els.stats.volume_unmonitored_count.innerText = json.result.unmonitored;
			library_els.stats.issue_count.innerText = json.result.issues;
			library_els.stats.issue_download_count.innerText = json.result.downloaded_issues;
			library_els.stats.file_count.innerText = json.result.files;
			library_els.stats.total_file_size.innerText =
				json.result.total_file_size > 0
					? convertSize(json.result.total_file_size)
					: '0 MB';
		});
}

function fetchDisplayMode(api_key) {
	return fetchAPI('/settings', api_key)
		.then(json => {
			state.displayMode = json.result.library_display_mode || 'virtual_scroll';
			if (state.displayMode !== 'virtual_scroll' && state.displayMode !== 'pagination')
				state.displayMode = 'virtual_scroll';
		});
}

function fetchPublishers(api_key) {
	return fetchAPI('/volumes/publishers', api_key)
		.then(json => {
			const selected =
				lib_options.lib_publisher_filter
				|| library_els.view_options.publisher.value;
			library_els.view_options.publisher.innerHTML = '<option value="">All Publishers</option>';
			json.result.forEach(publisher => {
				const option = document.createElement('option');
				option.value = publisher;
				option.innerText = publisher;
				library_els.view_options.publisher.appendChild(option);
			});
			library_els.view_options.publisher.value = selected;
		});
}

function hasActiveLibraryFilter() {
	return (
		library_els.view_options.status.value !== ''
		|| library_els.view_options.date.value !== ''
		|| library_els.search.input.value !== ''
		|| library_els.view_options.publisher.value !== ''
		|| library_els.view_options.description.value !== ''
	);
}

function getAllFilteredVolumeIds(api_key) {
	const params = {
		sort: library_els.view_options.sort.value,
		status_filter: library_els.view_options.status.value,
		date_filter: library_els.view_options.date.value,
		minimal: 'true',
		per_page: 0
	};

	const query = library_els.search.input.value;
	if (query !== '')
		params.query = query;

	const publisher = library_els.view_options.publisher.value;
	if (publisher !== '')
		params.publisher = publisher;

	const has_description = library_els.view_options.description.value;
	if (has_description !== '')
		params.has_description = has_description;

	return fetchAPI('/volumes', api_key, params)
		.then(json => (json.result.volumes || []).map(volume => volume.id));
}

function runUpdateAll(api_key) {
	if (cancelTask('update_all', api_key)) return;

	if (!hasActiveLibraryFilter()) {
		sendAPI('POST', '/system/tasks', api_key, {}, {
			'cmd': 'update_all',
			'allow_skipping': false
		})
		.then(() => fillTaskQueue(api_key));
		return;
	}

	library_els.mass_edit.progress.innerText = '';

	getAllFilteredVolumeIds(api_key)
		.then(volume_ids => {
			if (volume_ids.length === 0) {
				showLibraryPage(library_els.pages.empty);
				library_els.mass_edit.progress.innerText = 'No filtered volumes to update';
				return;
			}

			return sendAPI('POST', '/system/tasks', api_key, {}, {
				'cmd': 'update_all',
				'allow_skipping': false,
				'volume_ids': volume_ids
			})
			.then(() => {
				fillTaskQueue(api_key);
				library_els.mass_edit.progress.innerText = `Queued update for ${volume_ids.length} filtered volumes`;
			});
		});
}

function formatSearchOutcome(outcome) {
	const rejected = Object.values(outcome.rejections || {})
		.reduce((total, count) => total + count, 0);
	const failed = Object.values(outcome.enqueue_failures || {})
		.reduce((total, count) => total + count, 0);
	const parts = [
		`${outcome.volumes_scanned} volumes scanned`,
		`${outcome.open_issues} missing monitored issues`,
		`${outcome.candidates_found} candidates checked`,
		`${outcome.matched_candidates} matching candidates`,
		`${outcome.selected_links} download pages selected`,
		`${outcome.queued_links} queue entries added`,
		`${outcome.already_queued_links} already queued`,
		`${rejected} non-matching alternatives`
	];
	const rejection_reasons = Object.entries(outcome.rejections || {});
	if (rejection_reasons.length)
		parts.push(rejection_reasons
			.map(([reason, count]) => `${reason.replaceAll('_', ' ')}: ${count}`)
			.join(', '));
	const enqueue_failures = Object.entries(outcome.enqueue_failures || {});
	if (enqueue_failures.length)
		parts.push(`${failed} selected pages failed (${enqueue_failures
			.map(([reason, count]) => `${reason}: ${count}`)
			.join(', ')})`);
	if (outcome.volumes_skipped)
		parts.push(`${outcome.volumes_skipped} unmonitored volumes skipped`);
	return parts.join(' · ');
}

function runSearchMissing(api_key, refresh=false) {
	const task_name = refresh ? 'refresh_search_all' : 'search_all';
	if (cancelTask(task_name, api_key)) return;

	if (!hasActiveLibraryFilter()) {
		sendAPI('POST', '/system/tasks', api_key, {}, {
			'cmd': task_name
		})
		.then(() => fillTaskQueue(api_key));
		return;
	}

	library_els.mass_edit.progress.innerText = '';

	getAllFilteredVolumeIds(api_key)
		.then(volume_ids => {
			if (volume_ids.length === 0) {
				showLibraryPage(library_els.pages.empty);
				library_els.mass_edit.progress.innerText = 'No filtered volumes to search';
				return;
			}

			return sendAPI('POST', '/system/tasks', api_key, {}, {
				'volume_ids': volume_ids,
				'cmd': task_name
			})
			.then(() => {
				fillTaskQueue(api_key);
				library_els.mass_edit.progress.innerText = `Queued search for ${volume_ids.length} filtered volumes`;
			});
		});
}

function runAction(api_key, action, args={}) {
	showLibraryPage(library_els.pages.loading);

	sendAPI('POST', '/masseditor', api_key, {}, {
		'volume_ids': [...state.selectedVolumeIds],
		'action': action,
		'args': args
	})
		.then(() => {
			state.selectedVolumeIds.clear();
			library_els.mass_edit.select_all.checked = false;
			fetchLibrary(api_key);
		});
}

const lib_options = getLocalStorage(
	'lib_sorting',
	'lib_view',
	'lib_filter',
	'lib_status_filter',
	'lib_date_filter',
	'lib_page_size',
	'lib_publisher_filter',
	'lib_description_filter'
);

if (!lib_options.lib_status_filter && !lib_options.lib_date_filter) {
	const legacy_filter = lib_options.lib_filter;
	if (legacy_filter && legacy_filter.startsWith('recently_'))
		lib_options.lib_date_filter = legacy_filter;
	else if (legacy_filter === 'wanted')
		lib_options.lib_status_filter = 'missing_monitored';
	else if (legacy_filter === 'has_description')
		lib_options.lib_description_filter = 'true';
	else if (legacy_filter)
		lib_options.lib_status_filter = legacy_filter;

	if (legacy_filter)
		setLocalStorage({
			'lib_status_filter': lib_options.lib_status_filter || '',
			'lib_date_filter': lib_options.lib_date_filter || '',
			'lib_description_filter': lib_options.lib_description_filter || '',
			'lib_filter': ''
		});
}

library_els.view_options.sort.value = lib_options.lib_sorting;
library_els.view_options.view.value = lib_options.lib_view;
library_els.view_options.status.value = lib_options.lib_status_filter || '';
library_els.view_options.date.value = lib_options.lib_date_filter || '';
library_els.view_options.publisher.value = lib_options.lib_publisher_filter || '';
library_els.view_options.description.value = lib_options.lib_description_filter || '';

if (lib_options.lib_page_size !== null && lib_options.lib_page_size !== undefined) {
	paginationState.pageSize = parseInt(lib_options.lib_page_size) || 50;
}
library_els.pagination.size.value = String(paginationState.pageSize);

Promise.all([usingApiKey(), socketReady])
	.then(([api_key, activeSocket]) => {
		if (!api_key || !activeSocket) return null;
		return Promise.all([
			fetchDisplayMode(api_key),
			fetchPublishers(api_key)
		]).then(() => [api_key, activeSocket]);
	})
	.then(ready => {
		if (!ready) return;
		const [api_key, activeSocket] = ready;
		fetchLibrary(api_key);
		fetchStats(api_key);

		library_els.search.clear.onclick = () => clearSearch(api_key);

		library_els.task_buttons.update_all.onclick =
			() => runUpdateAll(api_key);
		library_els.task_buttons.search_all.onclick =
			() => runSearchMissing(api_key);
		library_els.task_buttons.refresh_search.onclick =
			() => runSearchMissing(api_key, true);

		library_els.view_options.sort.onchange = () => {
			setLocalStorage({'lib_sorting': library_els.view_options.sort.value});
			paginationState.currentPage = 1;
			fetchLibrary(api_key);
		};

		library_els.view_options.view.onchange = () => {
			setLocalStorage({'lib_view': library_els.view_options.view.value});
			renderLibrary(state.currentVolumes, api_key);
		};

		library_els.view_options.status.onchange = () => {
			setLocalStorage({'lib_status_filter': library_els.view_options.status.value});
			paginationState.currentPage = 1;
			fetchLibrary(api_key);
		};

		library_els.view_options.date.onchange = () => {
			setLocalStorage({'lib_date_filter': library_els.view_options.date.value});
			paginationState.currentPage = 1;
			fetchLibrary(api_key);
		};

		library_els.view_options.publisher.onchange = () => {
			setLocalStorage({'lib_publisher_filter': library_els.view_options.publisher.value});
			paginationState.currentPage = 1;
			fetchLibrary(api_key);
		};

		library_els.view_options.description.onchange = () => {
			setLocalStorage({'lib_description_filter': library_els.view_options.description.value});
			paginationState.currentPage = 1;
			fetchLibrary(api_key);
		};

		library_els.pagination.first.onclick = () => goToPage(1, api_key);
		library_els.pagination.prev.onclick = () => goToPage(paginationState.currentPage - 1, api_key);
		library_els.pagination.next.onclick = () => goToPage(paginationState.currentPage + 1, api_key);
		library_els.pagination.last.onclick = () => goToPage(paginationState.totalPages, api_key);
		library_els.pagination.size.onchange = () => {
			paginationState.pageSize = parseInt(library_els.pagination.size.value) || 50;
			paginationState.currentPage = 1;
			setLocalStorage({'lib_page_size': String(paginationState.pageSize)});
			fetchLibrary(api_key);
		};

		library_els.mass_edit.button.onclick =
		library_els.mass_edit.cancel.onclick = () => {
			const toggle = library_els.mass_edit.toggle;
			if (toggle.hasAttribute('checked'))
				toggle.removeAttribute('checked');
			else {
				const select = document.querySelector('select[name="root_folder_id"]');
				if (select.querySelector('option') === null) {
					fetchAPI('/rootfolder', api_key)
						.then(json => {
							json.result.forEach(rf => {
								const entry = document.createElement('option');
								entry.value = rf.id;
								entry.innerText = rf.folder;
								select.appendChild(entry);
							});
							toggle.setAttribute('checked', '');
						});
				} else
					toggle.setAttribute('checked', '');
			}
		};

		library_els.mass_edit.bar.querySelectorAll('.action-divider > button[data-action]').forEach(
			button => button.onclick = e => runAction(api_key, e.target.dataset.action)
		);

		library_els.mass_edit.bar.querySelector('button[data-action="delete"]').onclick =
			e => runAction(api_key, e.target.dataset.action, {
				'delete_folder': document.querySelector('select[name="delete_folder"]').value === 'true'
			});

		library_els.mass_edit.bar.querySelector('button[data-action="root_folder"]').onclick =
			e => runAction(api_key, e.target.dataset.action, {
				'root_folder_id': parseInt(document.querySelector('select[name="root_folder_id"]').value)
			});

		library_els.mass_edit.bar.querySelector('button[data-action="monitoring_scheme"]').onclick =
			e => runAction(api_key, e.target.dataset.action, {
				'monitoring_scheme': document.querySelector('select[name="monitoring_scheme"]').value
			});

		activeSocket.on(
			'downloaded_status',
			data => {
				const inst = new LibraryEntry(data.volume_id, api_key);
				if (inst.list_entry === null)
					return;
				const new_progress = inst.getProgress();
				new_progress[0] += data.downloaded_issues.length
								- data.not_downloaded_issues.length;
				inst.setProgressBar(new_progress[0], new_progress[1]);
			}
		);
		activeSocket.on(
			'mass_editor_status',
			data => library_els.mass_edit.progress.innerText = data.summary
				? formatSearchOutcome(data.summary)
				: `${data.current_item}/${data.total_items}`
		);
		activeSocket.on('task_ended', data => {
			if ([
				'update_all',
				'search_all',
				'refresh_search_all'
			].includes(data.action))
				fetchLibrary(api_key);
		});
	});
library_els.search.container.action = 'javascript:searchLibrary();';

library_els.mass_edit.select_all.onchange = () => {
	if (library_els.mass_edit.select_all.checked)
		state.currentVolumes.forEach(volume => state.selectedVolumeIds.add(volume.id));
	else
		state.selectedVolumeIds.clear();
	library_els.views.table.querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
		checkbox.checked = library_els.mass_edit.select_all.checked;
	});
};
