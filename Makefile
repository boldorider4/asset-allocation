.PHONY: web web-example web-clean

VISUALIZER := _visualizer
TEMPLATE := visual/web

web:
	mkdir -p $(VISUALIZER)/data
	cp -R $(TEMPLATE)/. $(VISUALIZER)/
	mkdir -p $(VISUALIZER)/data
	python -m visual.stamp_web

web-example: web
	python -m visual.web_example

web-clean:
	rm -rf $(VISUALIZER)
