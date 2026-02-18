class VirtualScroller {
	constructor(options) {
		this.container = options.container;
		this.renderItem = options.renderItem;
		this.data = options.data || [];
		this.mode = options.mode || 'posters';
		this.buffer = options.buffer || 10;
		this.posterWidth = options.posterWidth || 140;
		this.posterHeight = options.posterHeight || 280;
		this.posterGap = options.posterGap || 16;
		this.tableRowHeight = options.tableRowHeight || 56;
		this.scheduled = false;

		this.topSpacer = document.createElement('div');
		this.bottomSpacer = document.createElement('div');
		this.itemsContainer = document.createElement('div');
		this.itemsContainer.classList.add('virtual-items');

		this.container.innerHTML = '';
		this.container.appendChild(this.topSpacer);
		this.container.appendChild(this.itemsContainer);
		this.container.appendChild(this.bottomSpacer);
		this.container.classList.add('virtual-mode');

		this.boundOnScroll = () => this.scheduleRender();
		this.boundOnResize = () => this.scheduleRender();
		this.container.addEventListener('scroll', this.boundOnScroll);
		window.addEventListener('resize', this.boundOnResize);

		this.render(true);
	}

	setData(data) {
		this.data = data || [];
		this.container.scrollTop = 0;
		this.render(true);
	}

	setMode(mode) {
		this.mode = mode;
		this.container.scrollTop = 0;
		this.render(true);
	}

	scheduleRender() {
		if (this.scheduled)
			return;
		this.scheduled = true;
		requestAnimationFrame(() => {
			this.scheduled = false;
			this.render();
		});
	}

	calculateRange() {
		const total = this.data.length;
		const viewportHeight = Math.max(this.container.clientHeight || 0, 1);
		const scrollTop = this.container.scrollTop || 0;

		if (total === 0) {
			return {
				startIndex: 0,
				endIndex: 0,
				topPadding: 0,
				bottomPadding: 0
			};
		}

		if (this.mode === 'table') {
			const rowHeight = this.tableRowHeight;
			const startIndex = Math.max(
				0,
				Math.floor(scrollTop / rowHeight) - this.buffer
			);
			const visibleCount = Math.ceil(viewportHeight / rowHeight) + this.buffer * 2;
			const endIndex = Math.min(total, startIndex + visibleCount);
			const topPadding = startIndex * rowHeight;
			const bottomPadding = Math.max(0, (total - endIndex) * rowHeight);

			return { startIndex, endIndex, topPadding, bottomPadding };
		}

		const rowHeight = this.posterHeight + this.posterGap;
		const itemWidthWithGap = this.posterWidth + this.posterGap;
		const columns = Math.max(
			1,
			Math.floor((this.container.clientWidth + this.posterGap) / itemWidthWithGap)
		);
		const totalRows = Math.ceil(total / columns);
		const startRow = Math.max(0, Math.floor(scrollTop / rowHeight) - this.buffer);
		const visibleRows = Math.ceil(viewportHeight / rowHeight) + this.buffer * 2;
		const endRow = Math.min(totalRows, startRow + visibleRows);
		const startIndex = Math.max(0, startRow * columns);
		const endIndex = Math.min(total, endRow * columns);
		const topPadding = startRow * rowHeight;
		const bottomPadding = Math.max(0, (totalRows - endRow) * rowHeight);

		return { startIndex, endIndex, topPadding, bottomPadding };
	}

	render(force = false) {
		const range = this.calculateRange();
		const { startIndex, endIndex, topPadding, bottomPadding } = range;

		if (!Number.isFinite(startIndex) || !Number.isFinite(endIndex) || endIndex < startIndex) {
			const fallbackCount = Math.min(this.data.length, 50);
			this.topSpacer.style.height = '0px';
			this.bottomSpacer.style.height = '0px';
			this.itemsContainer.innerHTML = '';
			for (let index = 0; index < fallbackCount; index++) {
				this.itemsContainer.appendChild(this.renderItem(this.data[index], index));
			}
			return;
		}

		const nextSlice = this.data.slice(startIndex, endIndex);
		if (!force && this.startIndex === startIndex && this.endIndex === endIndex)
			return;

		this.startIndex = startIndex;
		this.endIndex = endIndex;

		this.topSpacer.style.height = `${topPadding}px`;
		this.bottomSpacer.style.height = `${bottomPadding}px`;
		this.itemsContainer.innerHTML = '';

		for (let index = 0; index < nextSlice.length; index++) {
			const absoluteIndex = startIndex + index;
			this.itemsContainer.appendChild(this.renderItem(nextSlice[index], absoluteIndex));
		}
	}

	destroy() {
		this.container.removeEventListener('scroll', this.boundOnScroll);
		window.removeEventListener('resize', this.boundOnResize);
		this.container.classList.remove('virtual-mode');
		this.container.innerHTML = '';
	}
}

window.VirtualScroller = VirtualScroller;
