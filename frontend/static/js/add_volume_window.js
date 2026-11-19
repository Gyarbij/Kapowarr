const AddVolumeWindow = {
	async open({volume, source, onAdded}) {
		const form = document.getElementById('add-form');
		const fields = form.elements;
		const submit = document.getElementById('add-volume');
		const autoSearch = document.getElementById('auto-search-input');
		const error = document.getElementById('add-error');
		const note = document.getElementById('add-source-note');
		const year = fields['add-series-year'];
		const folder = fields['volumefolder-input'];
		const root = fields['rootfolder-input'];
		const special = fields['specialoverride-input'];
		const request = Symbol();
		this.request = request;
		form.reset();
		autoSearch.checked = false;
		error.textContent = '';
		submit.disabled = true;
		form.onsubmit = event => event.preventDefault();
		year.onchange = special.onchange = null;
		folder.placeholder = '';
		root.replaceChildren();
		document.querySelector('#add-window h2').textContent = volume?.title || 'Add volume';
		const cover = document.getElementById('add-cover');
		cover.removeAttribute('src');
		document.getElementById('add-year-row').classList.add('hidden');
		note.classList.toggle('hidden', !source);
		note.textContent = source
			? `${source.provider}: ${source.external_id}. If this matches an existing series, its folder and monitoring settings stay unchanged.`
			: '';
		showWindow('add-window');

		try {
			const key = await usingApiKey();
			const [folders, details] = await Promise.all([
				fetchAPI('/rootfolder', key),
				source ? fetchAPI('/volumes/from-source', key, {
					source: encodeURIComponent(source.provider),
					source_id: encodeURIComponent(source.external_id)
				}).then(json => json.result) : volume
			]);
			if (this.request !== request) return;
			document.querySelector('#add-window h2').textContent =
				details.title + (details.year && !source ? ` (${details.year})` : '');
			cover.src = details.cover_link || `${url_base}/static/img/placeholder.svg`;
			root.replaceChildren(...folders.result.map(item => new Option(item.folder, item.id)));
			if (!root.options.length) {
				error.textContent = 'Add a root folder under Settings > Media Management first.';
				return;
			}
			const preferences = getLocalStorage('monitor_new_volume', 'monitor_new_issues', 'monitoring_scheme');
			fields['monitor-volume-input'].value = preferences.monitor_new_volume;
			fields['monitor-issues-input'].value = preferences.monitor_new_issues;
			fields['monitoring-scheme-input'].value = preferences.monitoring_scheme;
			year.value = details.year ?? '';
			document.getElementById('add-year-row').classList.toggle('hidden', !source);
			document.getElementById('add-year-note').textContent = {
				series: 'From publisher series metadata. You can correct it here.',
				first_issue: 'Suggested from the release date of issue #1.',
			}[details.year_source] || 'No series start year was supplied. Enter it if known.';

			let previewRequest = 0;
			async function previewFolder() {
				const preview = ++previewRequest;
				const response = await sendAPI('POST', '/volumes/search', key, {}, {
					comicvine_id: details.comicvine_id,
					title: details.title,
					year: year.valueAsNumber || details.year,
					volume_number: details.volume_number,
					publisher: details.publisher,
					special_version: special.value === 'auto' ? null : special.value || null
				});
				const json = await response.json();
				if (AddVolumeWindow.request === request && preview === previewRequest)
					folder.placeholder = json.result.folder;
			}
			year.onchange = special.onchange = () => {
				if (!year.validity.valid) return;
				previewFolder().catch(failure => {
					if (this.request !== request) return;
					console.error('Could not preview volume folder:', failure);
					folder.placeholder = '';
					error.textContent = 'Could not preview the automatic folder. You can still enter a custom folder.';
				});
			};
			await previewFolder();
			if (this.request !== request) return;
			submit.disabled = false;
			form.onsubmit = async event => {
				event.preventDefault();
				const options = {
					root_folder_id: Number(root.value),
					monitor: fields['monitor-volume-input'].value === 'true',
					monitor_new_issues: fields['monitor-issues-input'].value === 'true',
					monitoring_scheme: fields['monitoring-scheme-input'].value,
					volume_folder: folder.value,
					special_version: special.value || null,
					auto_search: autoSearch.checked
				};
				const identity = source ? {
					source: source.provider, source_id: String(source.external_id),
					year: year.valueAsNumber || null
				} : {comicvine_id: details.comicvine_id};
				setLocalStorage({
					monitor_new_volume: options.monitor,
					monitor_new_issues: options.monitor_new_issues,
					monitoring_scheme: options.monitoring_scheme
				});
				error.textContent = '';
				submit.disabled = true;
				showLoadWindow('add-window');
				let id;
				try {
					const result = await sendAPI('POST', source ? '/volumes/from-source' : '/volumes', key, {}, {
						...identity, ...options
					});
					id = (await result.json()).result.id;
				} catch (failure) {
					if (this.request !== request) return;
					const issue = await failure.json?.().catch(() => null);
					error.textContent = failure.status === 509 ? 'ComicVine API rate limit reached.'
						: issue?.error === 'VolumeFolderInvalid' ? 'Volume folder overlaps another library volume.'
						: source && ['source_id', 'issue_number'].includes(issue?.result?.key)
							? `Could not link publisher metadata: ${issue.result.value}.`
							: 'Could not add this volume. Check the options and try again.';
					submit.disabled = false;
					showWindow('add-window');
					return;
				}
				if (this.request !== request) return;
				closeWindow();
				onAdded(id);
			};
		} catch (failure) {
			if (this.request !== request) return;
			console.error('Could not prepare Add Volume:', failure);
			error.textContent = 'Could not prepare this volume. Check that the source is still available and try again.';
		}
	}
};
