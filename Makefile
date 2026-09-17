.PHONY: install web web-example web-clean clean serve stop-serve service stop-service

VISUALIZER := $(HOME)/.local/asalloc/visualizer
TEMPLATE := visual/web
INSTALL_ROOT := $(HOME)/.local/asalloc
INSTALL_VIS := $(INSTALL_ROOT)/visualizer
SYSTEMD_USER := $(HOME)/.config/systemd/user

install:
	pip install -e .
	mkdir -p $(HOME)/.local/bin
	@asalloc_bin="$$(command -v asalloc)" && \
	if [ -z "$$asalloc_bin" ]; then \
		echo "asalloc is not callable after install; check your PATH." >&2; exit 1; \
	elif [ "$$asalloc_bin" != "$(HOME)/.local/bin/asalloc" ]; then \
		ln -sf "$$asalloc_bin" $(HOME)/.local/bin/asalloc; \
		echo "Linked $(HOME)/.local/bin/asalloc -> $$asalloc_bin"; \
	else \
		echo "asalloc already provided by $(HOME)/.local/bin"; \
	fi

web:
	mkdir -p $(VISUALIZER)/data
	cp -R $(TEMPLATE)/. $(VISUALIZER)/
	mkdir -p $(VISUALIZER)/data
	python -m visual.stamp_web $(VISUALIZER)/index.html

web-example: web
	python -m visual.web_example

web-clean:
	find $(VISUALIZER)/data -name '*.raw' -delete 2>/dev/null || true
	rm -rf $(VISUALIZER)

clean: web-clean

serve: web
	@command -v asalloc >/dev/null 2>&1 || { echo "asalloc is not callable; run 'make install' first." >&2; exit 1; }
	asalloc serve --assets-file $(INSTALL_ROOT)/assets.json --cache-file $(INSTALL_ROOT)/cache.json

stop-serve:
	@command -v asalloc >/dev/null 2>&1 || { echo "asalloc is not callable; run 'make install' first." >&2; exit 1; }
	asalloc stop-serve

service:
	@command -v asalloc >/dev/null 2>&1 || { echo "asalloc is not callable; run 'make install' first." >&2; exit 1; }
	@command -v systemctl >/dev/null 2>&1 || { echo "systemctl not found; need a systemd Linux host." >&2; exit 1; }
	$(MAKE) web VISUALIZER=$(INSTALL_VIS)
	mkdir -p $(INSTALL_ROOT) $(INSTALL_VIS)/data $(SYSTEMD_USER)
	cp config.ini $(INSTALL_ROOT)/
	cp systemd/asalloc-serve.service systemd/asalloc-update.service systemd/asalloc-update.timer $(SYSTEMD_USER)/
	systemctl --user daemon-reload
	systemctl --user enable --now asalloc-serve.service
	systemctl --user enable --now asalloc-update.timer
	@echo "Installed user units. Place holdings at $(INSTALL_ROOT)/assets.json"
	@echo "Headless hosts: sudo loginctl enable-linger $$USER"

stop-service:
	@command -v systemctl >/dev/null 2>&1 || { echo "systemctl not found; need a systemd Linux host." >&2; exit 1; }
	systemctl --user disable --now asalloc-serve.service
	systemctl --user disable --now asalloc-update.timer
	-systemctl --user stop asalloc-update.service
	@echo "Stopped and disabled asalloc user units."
