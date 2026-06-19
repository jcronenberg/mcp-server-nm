NAME    := mcp-server-nm
VERSION := $(shell grep '^version' pyproject.toml | sed 's/.*"\(.*\)"/\1/')
TARBALL := $(NAME)-$(VERSION).tar.gz

.PHONY: test dist clean

test:
	python3 tests/run_tests.py

dist: $(TARBALL)

$(TARBALL):
	git archive --prefix=$(NAME)-$(VERSION)/ HEAD | gzip > $@

clean:
	rm -f $(TARBALL)
