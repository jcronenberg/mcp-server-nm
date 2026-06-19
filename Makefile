NAME    := mcp-server-nm
VERSION := $(shell grep '^version' pyproject.toml | sed 's/.*"\(.*\)"/\1/')
TARBALL := $(NAME)-$(VERSION).tar.gz

.PHONY: test check system-test unit-test dist clean

test check: system-test
	@if [ -z "$$NM_MCP_TEST_SERVER" ]; then python3 tests/test_unit.py; else echo "Skipping unit tests (NM_MCP_TEST_SERVER is set)"; fi

system-test:
	python3 tests/run_tests.py

unit-test:
	python3 tests/test_unit.py

dist: $(TARBALL)

$(TARBALL):
	git archive --prefix=$(NAME)-$(VERSION)/ HEAD | gzip > $@

clean:
	rm -f $(TARBALL)
