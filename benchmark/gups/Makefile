CC ?= gcc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -fopenmp
LDFLAGS ?= -fopenmp

TARGET := gups
SOURCES := gups.c

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(SOURCES)
	$(CC) $(CFLAGS) $(SOURCES) -o $(TARGET) $(LDFLAGS)

clean:
	rm -f $(TARGET)
