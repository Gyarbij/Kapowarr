const HistoryEls = {
	table: document.querySelector('#history'),
	table_container: document.querySelector('.activity-table-container'),
	status: document.querySelector('#history-status'),
	load_more: document.querySelector('#history-load-more'),
	category: document.querySelector('#history-category'),
	result: document.querySelector('#history-result'),
	buttons: {
		refresh: document.querySelector('#refresh-button'),
		clear: document.querySelector('#clear-button'),
		confirm_clear: document.querySelector('#submit-clear-history')
	}
};

let nextBeforeId = null;
let loading = false;

function historyParameters(append) {
	const parameters = {limit: 50};
	if (append && nextBeforeId !== null) parameters.before_id = nextBeforeId;
	if (HistoryEls.category.value) parameters.category = HistoryEls.category.value;
	if (HistoryEls.result.value) parameters.success = HistoryEls.result.value;
	return parameters;
};

function fillHistory(api_key, append=false) {
	if (loading) return;
	loading = true;
	HistoryEls.status.textContent = 'Loading activity...';
	HistoryEls.status.classList.remove('hidden');
	HistoryEls.load_more.disabled = true;

	if (!append) {
		nextBeforeId = null;
		HistoryEls.table.innerHTML = '';
	};

	fetchAPI('/activity/history', api_key, historyParameters(append))
	.then(json => {
		ActivityHistoryUI.appendActivities(HistoryEls.table, json.result.items);
		nextBeforeId = json.result.next_before_id;
		const empty = HistoryEls.table.children.length === 0;
		HistoryEls.status.textContent = empty ? 'No activity recorded' : '';
		HistoryEls.status.classList.toggle('hidden', !empty);
		HistoryEls.table_container.classList.toggle('hidden', empty);
		HistoryEls.load_more.classList.toggle('hidden', !json.result.has_more);
	})
	.catch(() => {
		HistoryEls.status.textContent = 'Could not load activity';
		HistoryEls.status.classList.remove('hidden');
	})
	.finally(() => {
		loading = false;
		HistoryEls.load_more.disabled = false;
	});
};

function clearHistory(api_key) {
	sendAPI('DELETE', '/activity/history', api_key)
	.then(() => {
		closeWindow();
		fillHistory(api_key);
	});
};

// code run on load
usingApiKey()
.then(api_key => {
	fillHistory(api_key);
	HistoryEls.buttons.refresh.onclick = e => fillHistory(api_key);
	HistoryEls.buttons.clear.onclick = e => showWindow('clear-history-window');
	HistoryEls.buttons.confirm_clear.onclick = e => clearHistory(api_key);
	HistoryEls.load_more.onclick = e => fillHistory(api_key, true);
	HistoryEls.category.onchange = e => fillHistory(api_key);
	HistoryEls.result.onchange = e => fillHistory(api_key);
});
