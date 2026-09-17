.PHONY: install web web-example web-clean serve stop-serve service stop-service

VISUALIZER := $(HOME)/.local/asalloc/visualizer
TEMPLATE := visual/web
PIDFILE := $(VISUALIZER)/.serve.pid
INSTALL_ROOT := $(HOME)/.local/asalloc
INSTALL_VIS := $(INSTALL_ROOT)/visualizer
SYSTEMD_USER := $(HOME)/.config/systemd/user

install:
	pip install -e .

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

serve:
	@command -v asalloc >/dev/null 2>&1 || { echo "asalloc is not callable; run 'make install' first." >&2; exit 1; }
	asalloc serve

stop-serve:
	@if [ -f "$(PIDFILE)" ]; then \
		pid=$$(cat "$(PIDFILE)"); \
		if kill $$pid 2>/dev/null; then \
			echo "Stopped visualizer server (pid $$pid)."; \
		else \
			echo "Visualizer server pid $$pid is not running."; \
		fi; \
		rm -f "$(PIDFILE)"; \
	else \
		echo "No visualizer server pid file; nothing to stop."; \
	fi

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
