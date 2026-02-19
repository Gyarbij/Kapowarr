function fillSettings(api_key) {
	fetchAPI('/settings', api_key)
	.then(json => {
		document.querySelector('#date-type-input').value = json.result.date_type;
		document.querySelector('#metadata-source-input').value = json.result.metadata_source || 'comicvine';
		document.querySelector('#comicvine-api-key-input').value = json.result.comicvine_api_key || '';
		document.querySelector('#metron-username-input').value = json.result.metron_username || '';
		document.querySelector('#metron-password-input').value = json.result.metron_password || '';
	});
};

function saveSettings(api_key) {
	document.querySelector("#save-button p").innerText = 'Saving';
	const data = {
		'date_type': document.querySelector('#date-type-input').value,
		'metadata_source': document.querySelector('#metadata-source-input').value,
		'comicvine_api_key': document.querySelector('#comicvine-api-key-input').value,
		'metron_username': document.querySelector('#metron-username-input').value,
		'metron_password': document.querySelector('#metron-password-input').value
	};
	sendAPI('PUT', '/settings', api_key, {}, data)
	.then(response => response.json())
	.then(json => {
		if (json.error !== null) return Promise.reject(json);
		document.querySelector("#save-button p").innerText = 'Saved';
	})
	.catch(e => {
		document.querySelector("#save-button p").innerText = 'Failed';
		console.log(e.error);
	});
};

function testMetadataSource(api_key, source, button) {
	const originalText = button.innerText;
	button.innerText = 'Testing...';
	button.disabled = true;
	
	sendAPI('POST', '/metadata/sources/test', api_key, {}, { source: source })
	.then(response => response.json())
	.then(json => {
		if (json.error !== null) return Promise.reject(json);
		button.innerText = json.result.valid ? '✓ Valid' : '✗ Invalid';
		button.style.color = json.result.valid ? 'var(--success-color)' : 'var(--error-color)';
		setTimeout(() => {
			button.innerText = originalText;
			button.style.color = '';
			button.disabled = false;
		}, 3000);
	})
	.catch(e => {
		button.innerText = '✗ Error';
		button.style.color = 'var(--error-color)';
		console.log(e.error);
		setTimeout(() => {
			button.innerText = originalText;
			button.style.color = '';
			button.disabled = false;
		}, 3000);
	});
}

// code run on load

usingApiKey()
.then(api_key => {
	fillSettings(api_key);
	document.querySelector('#save-button').onclick = e => saveSettings(api_key);
	
	// Test buttons
	document.querySelector('#test-comicvine-btn').onclick = e => {
		e.preventDefault();
		testMetadataSource(api_key, 'comicvine', e.target);
	};
	document.querySelector('#test-metron-btn').onclick = e => {
		e.preventDefault();
		testMetadataSource(api_key, 'metron', e.target);
	};
});
