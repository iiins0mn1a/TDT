# Makefile for the Shadow time syscall microbenchmark.

CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra
LDFLAGS ?= -lrt
SHADOW_BIN ?= ../../deps/shadow/build/src/main/shadow

TARGET := time_benchmark
SOURCE := time_benchmark.c

all: $(TARGET)

$(TARGET): $(SOURCE)
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(TARGET)
	rm -rf shadow.data experiment_results

test-native: $(TARGET)
	./$(TARGET) 100000

test-shadow: $(TARGET)
	$(SHADOW_BIN) shadow_time_test.yaml

test-shadow-strace: $(TARGET)
	$(SHADOW_BIN) --strace-logging-mode=standard shadow_time_test.yaml

analyze:
	@for f in shadow.data/hosts/*/time_benchmark.*.strace; do \
		if [ -f "$$f" ]; then \
			echo "Analyzing $$f"; \
			python3 scripts/analyze_strace.py "$$f"; \
		fi; \
	done

.PHONY: all clean test-native test-shadow test-shadow-strace analyze
