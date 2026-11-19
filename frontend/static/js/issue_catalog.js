const IssueCatalog = {
	sourceNames: {
		marvel: 'Marvel',
		dc: 'DC',
		comixnow: 'ComixNOW',
		lunar: 'Lunar',
		locg: 'League of Comic Geeks',
		getcomics: 'GetComics',
		library: 'Library'
	},
	statusNames: {
		downloaded: 'Downloaded',
		missing_monitored: 'Missing - monitored',
		missing_unmonitored: 'Not monitored',
		metadata_pending: 'Issue metadata pending',
		not_in_library: 'Publisher listing',
		ambiguous: 'Needs review'
	},

	element(tag, className='', text='') {
		const element = document.createElement(tag);
		element.className = className;
		element.textContent = text;
		return element;
	},

	externalURL(value) {
		if (!value) return null;
		try {
			const url = new URL(value);
			return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
		} catch (_error) {
			return null;
		}
	},

	dateValue(date) {
		return [
			date.getFullYear(),
			String(date.getMonth() + 1).padStart(2, '0'),
			String(date.getDate()).padStart(2, '0')
		].join('-');
	},

	dateRange(period) {
		const start = new Date();
		const end = new Date();
		if (period === 'upcoming') {
			start.setDate(start.getDate() + 1);
			end.setDate(end.getDate() + 60);
		} else if (period === 'week') {
			start.setDate(start.getDate() - (start.getDay() + 6) % 7);
			end.setTime(start.getTime());
			end.setDate(end.getDate() + 6);
		} else if (period === 'all') {
			start.setDate(start.getDate() - 60);
			end.setDate(end.getDate() + 60);
		} else {
			start.setDate(start.getDate() - 30);
		}
		return {
			start_date: this.dateValue(start),
			end_date: this.dateValue(end)
		};
	},

	publisherKey(name) {
		const text = (name || '').toLowerCase();
		if (text.includes('marvel')) return 'marvel';
		if (text === 'dc' || text.startsWith('dc ')) return 'dc';
		if (text.includes('image')) return 'image';
		const words = text.match(/[a-z0-9]+/g) || [];
		const ignored = new Set([
			'comic', 'comics', 'entertainment', 'publishing', 'publications',
			'studio', 'studios', 'press', 'books', 'media', 'productions',
			'group', 'inc', 'llc', 'ltd'
		]);
		const identifying = words.filter(word => !ignored.has(word));
		const key = (identifying.length ? identifying : words).join('');
		return ['2000ad', 'rebelliondevelopments'].includes(key) ? 'rebellion' : key;
	},

	addPublisherDialog(source) {
		return AddVolumeWindow.open({
			source,
			onAdded: id => { window.location.href = `${url_base}/volumes/${id}`; }
		});
	},

	card(issue) {
		const card = this.element('article', 'catalog-issue');
		card.dataset.recordKey = issue.record_key;
		card.dataset.status = issue.local_status;
		const cover = this.element('img', 'catalog-issue-cover');
		const placeholder = `${url_base}/static/img/placeholder.svg`;
		cover.src = this.externalURL(issue.cover_url) || placeholder;
		cover.alt = '';
		cover.loading = 'lazy';
		cover.addEventListener('error', () => { cover.src = placeholder; }, {once: true});
		card.appendChild(cover);

		const info = this.element('div', 'catalog-issue-info');
		const title = this.element('h3', 'catalog-issue-title');
		const number = issue.issue_number ?? issue.calculated_issue_number;
		const titleText = `${issue.series_title}${number == null ? '' : ` #${number}`}`;
		if (issue.local_volume_id) {
			const link = this.element('a', '', titleText);
			link.href = `${url_base}/volumes/${issue.local_volume_id}`;
			title.appendChild(link);
		} else {
			title.textContent = titleText;
		}
		info.appendChild(title);
		if (issue.issue_title)
			info.appendChild(this.element('p', 'catalog-issue-subtitle', issue.issue_title));
		info.appendChild(this.element('p', 'catalog-issue-publisher', issue.publisher || 'Unknown publisher'));
		if (issue.release_date) {
			const date = this.element('time', 'catalog-issue-date');
			date.dateTime = issue.release_date;
			date.textContent = new Date(`${issue.release_date}T12:00:00`)
				.toLocaleDateString(undefined, {year: 'numeric', month: 'short', day: 'numeric'});
			const upcoming = issue.release_date > this.dateValue(new Date());
			info.appendChild(this.element('span', 'catalog-release-state', upcoming ? 'Upcoming' : 'Released'));
			info.appendChild(date);
		}
		info.appendChild(this.element(
			'p', 'catalog-issue-sources',
			(issue.providers || []).map(source => this.sourceNames[source] || source).join(' / ')
		));
		info.appendChild(this.element(
			'span', 'catalog-issue-status',
			this.statusNames[issue.local_status] || 'Needs review'
		));

		const actions = this.element('div', 'catalog-issue-actions');
		const localLink = this.element(
			'a', 'catalog-action',
			issue.local_volume_id ? 'Open volume' : 'Find library series'
		);
		localLink.href = issue.local_volume_id
			? `${url_base}/volumes/${issue.local_volume_id}`
			: `${url_base}/add?q=${encodeURIComponent(issue.series_title)}`;
		actions.appendChild(localLink);

		const sources = [...(issue.provenance || [])].sort((a, b) => {
			const authority = ['marvel', 'dc', 'comixnow', 'lunar', 'locg', 'getcomics'];
			return authority.indexOf(a.provider) - authority.indexOf(b.provider);
		});
		const publisherSource = sources.find(source =>
			['marvel', 'dc', 'comixnow', 'lunar', 'locg'].includes(source.provider)
			&& source.external_id
		);
		if (publisherSource && !issue.local_issue_id && issue.local_status !== 'ambiguous') {
			const add = this.element('button', 'catalog-action', 'Add from publisher');
			add.type = 'button';
			add.onclick = () => this.addPublisherDialog(publisherSource);
			actions.appendChild(add);
		}
		const sourceURL = sources.map(source => this.externalURL(source.external_url))
			.find(url => url) || this.externalURL(issue.external_url);
		if (sourceURL) {
			const source = this.element('a', 'catalog-source-link', 'View source');
			source.href = sourceURL;
			source.target = '_blank';
			source.rel = 'noopener noreferrer';
			actions.appendChild(source);
		}
		info.appendChild(actions);
		card.appendChild(info);
		return card;
	}
};
