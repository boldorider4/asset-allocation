.PHONY: install web web-example web-clean serve

VISUALIZER := _visualizer
TEMPLATE := visual/web

install:
	pip install -e .

web:
	mkdir -p $(VISUALIZER)/data
	cp -R $(TEMPLATE)/. $(VISUALIZER)/
	mkdir -p $(VISUALIZER)/data
	python -m visual.stamp_web

web-example: web
	python -m visual.web_example

web-clean:
	rm -rf $(VISUALIZER)

serve:
	@command -v asalloc >/dev/null 2>&1 || { echo "asalloc is not callable; run 'make install' first." >&2; exit 1; }
	asalloc serve
