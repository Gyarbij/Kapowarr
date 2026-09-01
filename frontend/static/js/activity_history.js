const ActivityHistoryUI = (() => {
	const categoryLabels = {
		volume: 'Series',
		issue: 'Issue',
		file: 'File',
		download: 'Download'
	};

	function titleCase(value) {
		return value
			.replaceAll('_', ' ')
			.replace(/\b\w/g, character => character.toUpperCase());
	};

	function formatValue(value) {
		if (value === null || value === undefined) return 'None';
		if (typeof value === 'boolean') return value ? 'Yes' : 'No';
		if (Array.isArray(value)) return value.length ? value.join(', ') : 'None';
		if (typeof value === 'object') return JSON.stringify(value);
		return String(value);
	};

	function detailEntries(details) {
		const entries = [];
		Object.entries(details || {}).forEach(([key, value]) => {
			if (value === null || value === undefined) return;
			if (key === 'changes' && typeof value === 'object') {
				Object.entries(value).forEach(([field, change]) => {
					entries.push([
						titleCase(field),
						`${formatValue(change.from)} -> ${formatValue(change.to)}`,
						false
					]);
				});
				return;
			};
			entries.push([
				titleCase(key),
				formatValue(value),
				typeof value === 'string'
					&& key.endsWith('_link')
					&& /^https?:\/\//.test(value)
			]);
		});
		return entries;
	};

	function contextLabel(activity) {
		let volume = activity.volume_title || '';
		if (volume && activity.volume_year !== null)
			volume += ` (${activity.volume_year})`;

		let issue = '';
		if (activity.issue_number !== null) {
			issue = `#${activity.issue_number}`;
			if (activity.issue_title) issue += ` ${activity.issue_title}`;
		};

		const context = [volume, issue].filter(Boolean).join(' / ') || 'System';
		return activity.event_type.endsWith('_deleted')
			? `${context} (deleted)`
			: context;
	};

	function createCell(className, text) {
		const cell = document.createElement('td');
		cell.className = className;
		cell.textContent = text;
		return cell;
	};

	function appendActivity(tbody, activity) {
		const row = document.createElement('tr');
		row.className = `activity-entry activity-${activity.category}`;

		const timestamp = new Date(activity.created_at * 1000);
		row.appendChild(createCell(
			'activity-time',
			timestamp.toLocaleString([], {
				year: 'numeric',
				month: 'short',
				day: 'numeric',
				hour: '2-digit',
				minute: '2-digit'
			})
		));

		const typeCell = createCell(
			'activity-type',
			categoryLabels[activity.category] || titleCase(activity.category)
		);
		typeCell.title = titleCase(activity.event_type);
		row.appendChild(typeCell);

		const contextCell = document.createElement('td');
		contextCell.className = 'activity-context';
		const context = activity.volume_id === null
			? document.createElement('span')
			: document.createElement('a');
		context.textContent = contextLabel(activity);
		if (activity.volume_id !== null)
			context.href = `${url_base}/volumes/${activity.volume_id}`;
		contextCell.appendChild(context);
		row.appendChild(contextCell);

		const summaryCell = document.createElement('td');
		summaryCell.className = 'activity-summary';
		const summary = document.createElement('span');
		summary.textContent = activity.summary;
		summaryCell.appendChild(summary);

		const entries = detailEntries(activity.details);
		let detailRow = null;
		if (entries.length) {
			const expand = document.createElement('button');
			expand.type = 'button';
			expand.className = 'activity-expand';
			expand.textContent = '+';
			expand.title = 'Show details';
			expand.setAttribute('aria-expanded', 'false');
			summaryCell.appendChild(expand);

			detailRow = document.createElement('tr');
			detailRow.className = 'activity-detail hidden';
			const detailCell = document.createElement('td');
			detailCell.colSpan = 4;
			const list = document.createElement('dl');
			entries.forEach(([label, value, isLink]) => {
				const term = document.createElement('dt');
				term.textContent = label;
				const description = document.createElement('dd');
				if (isLink) {
					const link = document.createElement('a');
					link.href = value;
					link.target = '_blank';
					link.rel = 'noopener noreferrer';
					link.textContent = value;
					description.appendChild(link);
				} else {
					description.textContent = value;
				};
				list.append(term, description);
			});
			detailCell.appendChild(list);
			detailRow.appendChild(detailCell);

			expand.onclick = () => {
				const expanded = expand.getAttribute('aria-expanded') === 'true';
				expand.setAttribute('aria-expanded', String(!expanded));
				expand.textContent = expanded ? '+' : '-';
				expand.title = expanded ? 'Show details' : 'Hide details';
				detailRow.classList.toggle('hidden', expanded);
			};
		};

		row.appendChild(summaryCell);
		tbody.appendChild(row);
		if (detailRow !== null) tbody.appendChild(detailRow);
	};

	function appendActivities(tbody, activities) {
		activities.forEach(activity => appendActivity(tbody, activity));
	};

	return {appendActivities};
})();