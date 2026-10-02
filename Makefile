# Local development and test builds. Run `make help` for targets.

PYTHON_SYSTEM ?= python3
VENV          ?= .venv
PYTHON        := $(abspath $(VENV))/bin/python
VERSION       := $(shell tr -d '[:space:]' < res/version.txt)
APPIMAGE      := dist/RcloneDriveManager-$(VERSION)-x86_64.AppImage

# App data (config.json, logs) used by `make run` / `make run-appimage`. Kept separate
# from an installed copy's ~/.local/share/rclone-drive-manager so testing can't overwrite
# your real configuration. Your rclone config (remotes) is still used.
# Use `make run DATA_DIR=` to run against the real app data instead.
DATA_DIR      ?= $(CURDIR)/.dev-data
RUN_ENV       := $(if $(DATA_DIR),XDG_DATA_HOME="$(DATA_DIR)")

SOURCES       := $(wildcard src/*.py ui/*.ui res/*)

.PHONY: help venv compile run check appimage run-appimage deb rpm clean distclean

help:
	@echo "Targets:"
	@echo "  make venv          Create $(VENV) with PySide6 and python-appimage"
	@echo "  make compile       Generate src/ui_*.py and src/resources_rc.py"
	@echo "  make run           Run the app from source"
	@echo "  make check         Byte-compile and smoke-test startup (offscreen, warnings as errors)"
	@echo "  make appimage      Build $(APPIMAGE)"
	@echo "  make run-appimage  Build (if needed) and run the AppImage"
	@echo "  make deb / rpm     Build distro packages (packaging/ubuntu.sh, packaging/fedora.sh)"
	@echo "  make clean         Remove generated and build files"
	@echo "  make distclean     clean + remove $(VENV) and $(DATA_DIR)"
	@echo ""
	@echo "Variables: DATA_DIR=$(DATA_DIR) (empty = real app data), VENV=$(VENV)"

venv: $(VENV)/.installed

$(VENV)/.installed:
	$(PYTHON_SYSTEM) -m venv $(VENV)
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -m pip install --quiet "PySide6-Essentials==6.11.*" python-appimage
	touch $@

compile: src/resources_rc.py

src/resources_rc.py: $(VENV)/.installed ui/*.ui res/*
	$(PYTHON) compile.py

run: compile
	cd src && $(RUN_ENV) $(PYTHON) main.py

check: compile
	$(PYTHON) -m py_compile src/*.py
	@echo "Starting app offscreen for 5 seconds..."
	@cd src && XDG_DATA_HOME="$(CURDIR)/build/check-data" QT_QPA_PLATFORM=offscreen \
		timeout 5 $(PYTHON) -W error::DeprecationWarning main.py; \
		status=$$?; if [ $$status -eq 124 ]; then echo "check passed"; \
		else echo "app exited early with status $$status"; exit 1; fi

appimage: $(APPIMAGE)

$(APPIMAGE): $(VENV)/.installed $(SOURCES) $(filter-out packaging/appimage/build,$(wildcard packaging/appimage/*))
	PYTHON=$(PYTHON) PYTHON_APPIMAGE=$(abspath $(VENV))/bin/python-appimage \
		packaging/appimage/build.sh

run-appimage: $(APPIMAGE)
	$(RUN_ENV) $(APPIMAGE)

deb: compile
	packaging/ubuntu.sh

rpm: compile
	packaging/fedora.sh

clean:
	rm -rf build dist packaging/build packaging/appimage/build src/__pycache__
	rm -f src/ui_*.py src/*_rc.py

distclean: clean
	rm -rf $(VENV) $(DATA_DIR)
