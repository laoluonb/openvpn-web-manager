.PHONY: test demo

PYTHON ?= python3

test:
	$(PYTHON) -m compileall -q backend scripts tests
	$(PYTHON) -m unittest discover -s tests -v
	node --check web/app.js
	bash -n install.sh uninstall.sh scripts/openvpn-manager-firewall

demo:
	$(PYTHON) backend/server.py --demo --host 127.0.0.1 --port 9090
