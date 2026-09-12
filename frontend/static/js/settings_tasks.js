const inputs = {
	update_all_on_startup: document.querySelector('#update-all-on-startup-input'),
	search_all_on_startup: document.querySelector('#search-all-on-startup-input'),
	refresh_releases_on_startup: document.querySelector('#refresh-releases-on-startup-input'),
	startup_task_delay: document.querySelector('#startup-task-delay-input'),
	update_all_interval: document.querySelector('#update-all-interval-input'),
	search_all_interval: document.querySelector('#search-all-interval-input'),
	refresh_release_cache_interval: document.querySelector('#refresh-release-cache-interval-input'),
	refresh_release_discovery_interval: document.querySelector('#refresh-release-discovery-interval-input'),
	scheduled_update_skip_recent: document.querySelector('#skip-recent-input'),
	refresh_skip_window: document.querySelector('#refresh-skip-window-input')
};
const scheduledTasks = ['update_all', 'search_all', 'refresh_release_cache', 'refresh_release_discovery'];

function taskInput(task, suffix) {
	return document.querySelector(`#${task.replaceAll('_', '-')}-${suffix}-input`);
}

function updateScheduleControls(task) {
	const mode = taskInput(task, 'schedule').value;
	const interval = taskInput(task, 'interval');
	const weekday = taskInput(task, 'weekday');
	const time = taskInput(task, 'time');
	interval.hidden = mode !== 'interval';
	weekday.hidden = mode !== 'weekly';
	time.hidden = !['daily', 'weekly'].includes(mode);
}

function fillSettings(api_key) {
	fetchAPI('/settings', api_key)
	.then(json => {
		inputs.update_all_on_startup.checked = json.result.update_all_on_startup;
		inputs.search_all_on_startup.checked = json.result.search_all_on_startup;
		inputs.refresh_releases_on_startup.checked = json.result.refresh_releases_on_startup;
		inputs.startup_task_delay.value = json.result.startup_task_delay;
		inputs.update_all_interval.value = json.result.update_all_interval;
		inputs.search_all_interval.value = json.result.search_all_interval;
		inputs.refresh_release_cache_interval.value = json.result.refresh_release_cache_interval;
		inputs.refresh_release_discovery_interval.value = json.result.refresh_release_discovery_interval;
		scheduledTasks.forEach(task => {
			const legacyDisabled = json.result[`${task}_interval`] === 0;
			taskInput(task, 'schedule').value = legacyDisabled ? 'disabled' : json.result[`${task}_schedule`];
			taskInput(task, 'weekday').value = json.result[`${task}_weekday`];
			taskInput(task, 'time').value = json.result[`${task}_time`];
			updateScheduleControls(task);
		});
		inputs.scheduled_update_skip_recent.checked = json.result.scheduled_update_skip_recent;
		inputs.refresh_skip_window.value = json.result.refresh_skip_window;
	});
};

function saveSettings(api_key) {
	document.querySelector("#save-button p").innerText = 'Saving';
	Object.values(inputs).forEach(input => input.classList.remove('error-input'));
	const data = {
		'update_all_on_startup': inputs.update_all_on_startup.checked,
		'search_all_on_startup': inputs.search_all_on_startup.checked,
		'refresh_releases_on_startup': inputs.refresh_releases_on_startup.checked,
		'startup_task_delay': parseInt(inputs.startup_task_delay.value),
		'update_all_interval': parseInt(inputs.update_all_interval.value),
		'search_all_interval': parseInt(inputs.search_all_interval.value),
		'refresh_release_cache_interval': parseInt(inputs.refresh_release_cache_interval.value),
		'refresh_release_discovery_interval': parseInt(inputs.refresh_release_discovery_interval.value),
		'scheduled_update_skip_recent': inputs.scheduled_update_skip_recent.checked,
		'refresh_skip_window': parseInt(inputs.refresh_skip_window.value)
	};
	scheduledTasks.forEach(task => {
		data[`${task}_schedule`] = taskInput(task, 'schedule').value;
		data[`${task}_weekday`] = parseInt(taskInput(task, 'weekday').value);
		data[`${task}_time`] = taskInput(task, 'time').value;
	});
	sendAPI('PUT', '/settings', api_key, {}, data)
	.then(response =>
		document.querySelector("#save-button p").innerText = 'Saved'
	)
	.catch(e => {
		document.querySelector("#save-button p").innerText = 'Failed';
		e.json().then(error => {
			if (error.error === 'InvalidKeyValue') {
				const input = inputs[error.result.key];
				if (input) input.classList.add('error-input');
			}
		});
	});
};

usingApiKey()
.then(api_key => {
	fillSettings(api_key);
	scheduledTasks.forEach(task => {
		taskInput(task, 'schedule').onchange = () => updateScheduleControls(task);
	});
	document.querySelector('#save-button').onclick = e => saveSettings(api_key);
});
